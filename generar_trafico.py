"""Genera tráfico UDP con iperf3 de A hacia B y captura la traza en el receptor B.

Requiere la topología creada con:   bash red.sh up        (como root, en WSL)
Uso:                                python3 generar_trafico.py [--minutos 10] [--semilla 42]

Encadena fases aleatorias (reproducibles con la semilla):
  - tasa ofrecida:   uniforme entre 2 y 16 Mbit/s  (el cuello de botella es 10 Mbit/s)
  - duración:        uniforme entre 5 y 15 s
  - tamaño datagrama: uniforme entre 400 y 1400 bytes
hasta acumular los minutos pedidos de tráfico.

Salidas en datos/:
  traza.pcap   captura en vethB (receptor), primeros 96 bytes de cada paquete, marca en ns
  fases.csv    calendario real de fases + desfase entre reloj real y monotónico
  iperf/       resultado JSON de iperf3 por fase
  tc_fin.txt   contadores finales de la cola TBF en A

Por qué se registra el desfase de relojes: iperf3 (>= 3.17) marca cada datagrama con el
reloj MONOTÓNICO, mientras tcpdump usa el reloj REAL. Como A y B comparten kernel, basta
conocer  desfase = real - monotónico  para obtener el retardo exacto de cada paquete.
"""
import argparse
import csv
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DESTINO_B = "192.168.50.20"
PAUSA_S = 1.0


def desfase_relojes(muestras: int = 5) -> float:
    """real - monotónico, tomando la lectura con menor separación entre ambos relojes."""
    mejor = None
    for _ in range(muestras):
        a = time.clock_gettime(time.CLOCK_REALTIME)
        m = time.clock_gettime(time.CLOCK_MONOTONIC)
        b = time.clock_gettime(time.CLOCK_REALTIME)
        cand = ((a + b) / 2 - m, b - a)
        if mejor is None or cand[1] < mejor[1]:
            mejor = cand
    return mejor[0]


def ns(namespace: str, *cmd) -> list:
    return ["ip", "netns", "exec", namespace, *cmd]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=10)
    ap.add_argument("--semilla", type=int, default=42)
    args = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit("Ejecuta como root.")
    if "A" not in subprocess.run(["ip", "netns", "list"], capture_output=True, text=True).stdout:
        sys.exit("Falta la topología: bash red.sh up")

    rng = random.Random(args.semilla)
    fases, total = [], 0.0
    while total < args.minutos * 60:
        f = {"tasa_mbit": rng.randint(2, 16), "duracion_s": rng.randint(5, 15), "tamano_b": rng.randint(400, 1400)}
        fases.append(f)
        total += f["duracion_s"]
    print(f"{len(fases)} fases, {total/60:.1f} min de tráfico (semilla {args.semilla})")

    tmp = Path(tempfile.mkdtemp(prefix="traza_"))     # disco de Linux: no retrasa la captura
    (tmp / "iperf").mkdir()
    pcap = tmp / "traza.pcap"
    log_tcpdump = open(tmp / "tcpdump.log", "w")
    cap = subprocess.Popen(
        ns("B", "tcpdump", "-i", "vethB", "-nn", "-s", "96", "-B", "8192", "-Z", "root",
           "--time-stamp-precision=nano", "-w", str(pcap), f"udp and dst host {DESTINO_B} and dst port 5201"),
        stdout=subprocess.DEVNULL, stderr=log_tcpdump)
    time.sleep(2)

    filas = []
    try:
        for i, f in enumerate(fases, 1):
            off = desfase_relojes()
            t0 = time.time()
            print(f"  fase {i:2d}/{len(fases)}: {f['tasa_mbit']:2d} Mbit/s, {f['duracion_s']:2d} s, "
                  f"{f['tamano_b']:4d} B", flush=True)
            with open(tmp / "iperf" / f"fase{i:02d}.json", "w") as out:
                r = subprocess.run(
                    ns("A", "iperf3", "-c", DESTINO_B, "-u", "-b", f"{f['tasa_mbit']}M",
                       "-l", str(f["tamano_b"]), "-t", str(f["duracion_s"]), "-J"),
                    stdout=out, stderr=subprocess.DEVNULL)
            t1 = time.time()
            filas.append({"fase": i, **f, "inicio_epoch": f"{t0:.6f}", "fin_epoch": f"{t1:.6f}",
                          "desfase_real_mono": f"{off:.9f}", "iperf_ok": int(r.returncode == 0)})
            time.sleep(PAUSA_S)
        time.sleep(2)
    finally:
        cap.send_signal(signal.SIGINT)
        cap.wait()
        log_tcpdump.close()

    off_fin = desfase_relojes()
    with open(tmp / "fases.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()))
        w.writeheader()
        w.writerows(filas)
        # fila final solo con el desfase al terminar (para verificar deriva)
    (tmp / "desfase_fin.txt").write_text(f"{off_fin:.9f}\n")
    (tmp / "tc_fin.txt").write_text(subprocess.run(ns("A", "tc", "-s", "qdisc", "show", "dev", "vethA"),
                                                   capture_output=True, text=True).stdout)

    destino = RAIZ / "datos"
    destino.mkdir(exist_ok=True)
    for p in tmp.iterdir():
        if p.is_dir():
            shutil.copytree(p, destino / p.name, dirs_exist_ok=True)
        else:
            shutil.copy2(p, destino / p.name)
    shutil.rmtree(tmp)

    derivas = [float(x["desfase_real_mono"]) for x in filas] + [off_fin]
    print("\n" + (destino / "tcpdump.log").read_text().strip())
    print((destino / "tc_fin.txt").read_text().strip())
    print(f"Deriva del desfase de relojes durante la captura: {(max(derivas)-min(derivas))*1e3:.3f} ms")
    print(f"Fases con error de iperf3: {sum(1 - x['iperf_ok'] for x in filas)}")
    print(f"Listo: {destino}")


if __name__ == "__main__":
    main()
