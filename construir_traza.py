"""Convierte la captura del receptor (datos/traza.pcap) en datos/traza.csv: una fila por paquete.

Uso:  python3 construir_traza.py [--umbral-ms 5]

Cada datagrama UDP de iperf3 empieza con 12 bytes: segundos (4), microsegundos (4) y número de
secuencia (4), en orden de red. Con eso y la marca de captura en B se obtiene:

  retardo_ms = llegada a B (reloj real) - envío en A (reloj monotónico + desfase)

Columnas de traza.csv
  tiempo_s           instante de llegada a B, desde el primer paquete (s)
  flujo              puerto de origen (una prueba de iperf3 = un flujo)
  seq                número de secuencia del datagrama en su flujo
  --- atributos (entradas del árbol; solo usan información disponible al llegar el paquete) ---
  tamano_b           longitud de trama (bytes)
  iat_ms             tiempo desde el paquete anterior (entre-llegadas)
  iat_media_50       media de iat en los últimos 50 paquetes
  iat_std_50         desviación de iat en los últimos 50 paquetes (jitter)
  tasa_100ms_mbit    Mbit/s recibidos en los últimos 100 ms
  paquetes_100ms     paquetes recibidos en los últimos 100 ms
  tasa_1s_mbit       Mbit/s recibidos en el último segundo
  salto_seq          paquetes perdidos justo antes de este (salto en la secuencia)
  perdidas_100ms     paquetes perdidos detectados en los últimos 100 ms
  --- etiqueta ---
  retardo_ms         retardo A -> B del paquete (NO es atributo: define la etiqueta)
  congestion         1 si retardo_ms > umbral (5 ms por defecto), 0 si no
  --- solo para interpretar (no usar como atributos) ---
  info_fase, info_tasa_ofrecida_mbit
"""
import argparse
import struct
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent
DATOS = RAIZ / "datos"


def leer_pcap(ruta: Path) -> pd.DataFrame:
    """Lector mínimo de pcap (Ethernet/IPv4/UDP) para no depender de librerías externas."""
    b = ruta.read_bytes()
    magia = b[:4]
    if magia == b"\x4d\x3c\xb2\xa1":
        endian, escala = "<", 1e-9
    elif magia == b"\xd4\xc3\xb2\xa1":
        endian, escala = "<", 1e-6
    elif magia == b"\xa1\xb2\x3c\x4d":
        endian, escala = ">", 1e-9
    elif magia == b"\xa1\xb2\xc3\xd4":
        endian, escala = ">", 1e-6
    else:
        raise ValueError("No es un archivo pcap clásico")
    rec = struct.Struct(endian + "IIII")
    hdr = struct.Struct(">III")
    t_cap, largo, puerto, sec, usec, seq = [], [], [], [], [], []
    off, n = 24, len(b)
    while off + 16 <= n:
        ts_s, ts_f, incl, orig = rec.unpack_from(b, off)
        off += 16
        d = b[off:off + incl]
        off += incl
        if len(d) < 34 or d[12:14] != b"\x08\x00" or d[23] != 17:
            continue
        u = 14 + (d[14] & 0x0F) * 4
        if len(d) < u + 8 + 12:
            continue
        largo_udp = int.from_bytes(d[u + 4:u + 6], "big")
        if largo_udp - 8 < 12:              # mensajes de inicialización de iperf3 (4 bytes)
            continue
        s, us, q = hdr.unpack_from(d, u + 8)
        t_cap.append(ts_s + ts_f * escala)
        largo.append(orig)
        puerto.append(int.from_bytes(d[u:u + 2], "big"))
        sec.append(s); usec.append(us); seq.append(q)
    return pd.DataFrame({"t_cap": t_cap, "tamano_b": largo, "flujo": puerto,
                         "t_env_mono": np.array(sec, dtype=float) + np.array(usec, dtype=float) * 1e-6,
                         "seq": seq})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--umbral-ms", type=float, default=5.0)
    args = ap.parse_args()

    p = leer_pcap(DATOS / "traza.pcap").sort_values("t_cap", kind="stable", ignore_index=True)
    fases = pd.read_csv(DATOS / "fases.csv")
    off_fin = float((DATOS / "desfase_fin.txt").read_text())

    # Desfase real - monotónico interpolado en el tiempo (corrige posibles ajustes del reloj)
    t_ref = np.append(fases["inicio_epoch"].to_numpy(), fases["fin_epoch"].iat[-1] + 2)
    o_ref = np.append(fases["desfase_real_mono"].to_numpy(), off_fin)
    desfase = np.interp(p["t_cap"], t_ref, o_ref)
    p["retardo_ms"] = (p["t_cap"] - (p["t_env_mono"] + desfase)) * 1e3

    # Atributos observables en el receptor
    p["iat_ms"] = p["t_cap"].diff() * 1e3
    p["iat_media_50"] = p["iat_ms"].rolling(50, min_periods=50).mean()
    p["iat_std_50"] = p["iat_ms"].rolling(50, min_periods=50).std()
    idx = pd.to_datetime(p["t_cap"], unit="s")
    bits = pd.Series(p["tamano_b"].to_numpy() * 8.0, index=idx)
    p["tasa_100ms_mbit"] = bits.rolling("100ms").sum().to_numpy() / 0.1 / 1e6
    p["paquetes_100ms"] = bits.rolling("100ms").count().to_numpy().astype(int)
    p["tasa_1s_mbit"] = bits.rolling("1s").sum().to_numpy() / 1e6
    prev = p.groupby("flujo")["seq"].shift(1)
    p["salto_seq"] = np.where(prev.isna(), p["seq"] - 1, p["seq"] - prev - 1).clip(min=0).astype(int)
    p["perdidas_100ms"] = pd.Series(p["salto_seq"].to_numpy(), index=idx).rolling("100ms").sum().to_numpy().astype(int)

    # Etiqueta
    p["congestion"] = (p["retardo_ms"] > args.umbral_ms).astype(int)

    # Fase (solo interpretación)
    k = np.searchsorted(fases["inicio_epoch"].to_numpy(), p["t_cap"].to_numpy(), side="right") - 1
    k = k.clip(0, len(fases) - 1)
    p["info_fase"] = fases["fase"].to_numpy()[k]
    p["info_tasa_ofrecida_mbit"] = fases["tasa_mbit"].to_numpy()[k]

    p["tiempo_s"] = p["t_cap"] - p["t_cap"].iat[0]
    p = p.dropna(subset=["iat_media_50", "iat_std_50"]).reset_index(drop=True)

    cols = ["tiempo_s", "flujo", "seq", "tamano_b", "iat_ms", "iat_media_50", "iat_std_50",
            "tasa_100ms_mbit", "paquetes_100ms", "tasa_1s_mbit", "salto_seq", "perdidas_100ms",
            "retardo_ms", "congestion", "info_fase", "info_tasa_ofrecida_mbit"]
    out = p[cols].copy()
    out["tiempo_s"] = out["tiempo_s"].round(6)
    for c in ["iat_ms", "iat_media_50", "iat_std_50", "tasa_100ms_mbit", "tasa_1s_mbit", "retardo_ms"]:
        out[c] = out[c].round(4)

    out.to_csv(DATOS / "traza.csv", index=False)
    out.to_csv(DATOS / "traza.csv.gz", index=False, compression="gzip")

    # Resumen
    r = out["retardo_ms"]
    print(f"Paquetes en la traza: {len(out):,}")
    print(f"Retardo (ms): mín {r.min():.3f} | mediana {r.median():.3f} | p95 {r.quantile(.95):.3f} | máx {r.max():.3f}")
    print(f"Congestión (retardo > {args.umbral_ms:g} ms): {out['congestion'].mean()*100:.1f} % de los paquetes")
    print(f"Paquetes perdidos detectados por secuencia: {out['salto_seq'].sum():,}")
    print("\nPor tasa ofrecida (solo interpretación):")
    print(out.groupby("info_tasa_ofrecida_mbit").agg(paquetes=("congestion", "size"),
                                                      congestion=("congestion", "mean"),
                                                      retardo_mediano=("retardo_ms", "median"),
                                                      tasa_100ms=("tasa_100ms_mbit", "mean"),
                                                      perdidas=("salto_seq", "sum")).round(3).to_string())
    for f in ("traza.csv", "traza.csv.gz"):
        print(f"{f}: {(DATOS / f).stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
