# Detección de congestión en un enlace con árboles de decisión

Se genera tráfico UDP con iperf3 entre dos equipos A y B, se captura la traza en el receptor y se entrena un árbol de decisión (scikit-learn, criterio Gini) que clasifica si el enlace está congestionado en este momento, a partir de lo que se observa en el receptor.

```
[A] emisor  192.168.50.10  vethA ==== vethB  192.168.50.20  receptor [B]
    cliente iperf3                              servidor iperf3
    cola TBF 10 Mbit/s (cuello de botella)      captura tcpdump → traza
```

Los dos equipos se emulan en un solo computador con WSL2 (Ubuntu) y dos network namespaces unidos por un par `veth`. Como A y B comparten el reloj del mismo kernel, el retardo de cada paquete se mide sin error de sincronización.

## Definición de congestión

Un paquete está congestionado si su retardo de A a B supera 5 ms. Sin carga, el retardo es de unos 0,01 ms; con la cola llena, de unos 26 ms (32 kB a 10 Mbit/s). Con 5 ms hay unos 6 kB en cola: la cola ya se acumula, aunque todavía no se desborde.

## Contenido

| Archivo | Qué hace |
|---|---|
| [`red.sh`](red.sh) | Crea o elimina la topología A/B (namespaces, veth, cola TBF y servidor iperf3) |
| [`generar_trafico.py`](generar_trafico.py) | Encadena fases aleatorias de iperf3 (2–16 Mbit/s, 5–15 s, 400–1400 B; semilla fija) y captura en B |
| [`construir_traza.py`](construir_traza.py) | Convierte la captura en `datos/traza.csv`: una fila por paquete, 9 atributos, retardo y etiqueta |
| [`arbol_congestion.ipynb`](arbol_congestion.ipynb) | Notebook: exploración, partición temporal 60/20/20, selección del árbol, evaluación y conclusiones |
| [`informe/informe.pdf`](informe/informe.pdf) | Informe del trabajo: montaje, datos, metodología, resultados y conclusiones |
| `datos/traza.csv.gz` | Traza capturada: 723.688 paquetes en 11 minutos y 60 fases |
| `datos/fases.csv`, `datos/iperf/` | Calendario de fases y resultados JSON de iperf3 (solo para interpretar) |

### Columnas de la traza

- Atributos (entradas del árbol): `tamano_b`, `iat_ms`, `iat_media_50`, `iat_std_50`, `tasa_100ms_mbit`, `paquetes_100ms`, `tasa_1s_mbit`, `salto_seq`, `perdidas_100ms`.
- Etiqueta: `congestion` (1 si `retardo_ms` > 5).
- No se usan como entrada: `retardo_ms`, porque define la etiqueta; `info_*`, porque es el calendario de iperf3; y `tiempo_s`, `flujo` y `seq`, porque son identificadores.

## Resultados (prueba: último 20 % de la traza)

Se compararon árboles con profundidad máxima 1, 3 y sin límite. El de mayor F1 en validación fue `max_depth=None` (profundidad 15, 102 hojas):

| Exactitud | Precisión | Sensibilidad | F1 |
|---|---|---|---|
| 0,9936 | 0,9895 | 0,9980 | 0,9937 |

Casi todo el poder predictivo está en `tasa_100ms_mbit`: con la cola llena, el cuello de botella entrega exactamente unos 10 Mbit/s. Los errores se concentran cerca de las transiciones entre estados. El análisis completo está en la sección 9 del notebook y en el [informe](informe/informe.pdf).

## Cómo reproducirlo

Notebook (Google Colab): abre `arbol_congestion.ipynb` en Colab, ejecuta todo y sube `datos/traza.csv.gz` cuando aparezca el diálogo.

Nueva captura (WSL2 con Ubuntu):

```bash
sudo apt install -y iperf3 tcpdump python3-pandas python3-numpy
sudo bash red.sh up
sudo python3 generar_trafico.py --minutos 10 --semilla 42
python3 construir_traza.py --umbral-ms 5
sudo bash red.sh down
```

Requiere iperf3 3.17 o superior, que marca cada datagrama con el reloj monotónico.

`datos/traza.csv` (59 MB) y `datos/traza.pcap` (77 MB) no se incluyen por tamaño: el primero se regenera con `construir_traza.py` y es igual a `traza.csv.gz` sin comprimir.

## Limitaciones

El enlace es emulado (un solo flujo UDP y una cola TBF fija), la etiqueta depende del umbral de 5 ms y el modelo clasifica el estado actual, sin anticipar congestiones futuras.
