# Cómo funciona: del dato crudo al PID

Recorrido completo de una muestra: desde que sale del PLC hasta que se convierte
en una constante de sintonía. Cada etapa dice **qué calcula**, **con qué
fórmula** y **por qué se hace así**.

---

## Índice

- [El recorrido completo](#el-recorrido-completo)
- [Etapa 1 — Captura y escalado](#etapa-1--captura-y-escalado)
- [Etapa 2 — Los gráficos](#etapa-2--los-gráficos)
- [Etapa 3 — Detección del escalón](#etapa-3--detección-del-escalón)
- [Etapa 4 — La ventana de identificación](#etapa-4--la-ventana-de-identificación)
- [Etapa 5 — La línea base](#etapa-5--la-línea-base)
- [Etapa 6 — Los tres modelos](#etapa-6--los-tres-modelos)
- [Etapa 7 — El orden y el ranking](#etapa-7--el-orden-y-el-ranking)
- [Etapa 8 — Sintonía PID](#etapa-8--sintonía-pid)
- [Los avisos: cuándo no fiarse](#los-avisos-cuándo-no-fiarse)

---

## El recorrido completo

```
PLC (OPC UA)
   │  1 petición por muestra (get_values por lotes)
   ▼
PLCReader ──── mapping: qué variable es actuador / sensor / setpoint
   │
   ▼
RealtimeService ──── convierte a % de span y guarda en buffer circular
   │
   ├──────────────► WebSocket  ──► gráficos del paso 3
   │
   ▼  (al pulsar "Identificar")
StepDetectorService ──── ¿dónde está el escalón?
   │
   ▼
extract_window ──── recorta pre + post muestras alrededor
   │
   ▼
SignalProcessor.detect_step_info ──── K, línea base, instante del salto
   │
   ├──► FOPDTIdentifier        ─┐
   ├──► SOPDTIdentifier        ─┤──► R² de cada uno
   └──► IntegratingIdentifier  ─┘
   │
   ▼
ModelRanker ──── ordena por R²
   │
   ▼
ControllerTuner ──── IMC / ZN / Cohen-Coon / SIMC
```

---

## Etapa 1 — Captura y escalado

### Lectura del PLC

`PLCReader` corre en su propio hilo y consulta el PLC cada `period_s`. La
estructura del programa (qué variables hay, dónde vive el valor de cada una) se
resuelve **una vez por conexión** y se cachea; después cada muestra es **una
sola petición** `get_values`.

> Esto es lo que hace viable muestrear a 20 ms. Resolviendo el árbol en cada
> ciclo se iban ~19 idas y vueltas por muestra — casi mil por segundo a 20 ms,
> imposible sobre TCP.

El bucle se programa contra un **deadline acumulado**, no durmiendo el periodo
completo después de trabajar. Si se durmiera `period_s` tras cada lectura, el
periodo real sería `lectura + period_s` y el muestreo se degradaría solo.

### Conversión a porcentaje

Todo el motor trabaja internamente en **% de span**. La conversión es lineal:

```
%     = (valor − mínimo) / (máximo − mínimo) × 100
valor = mínimo + % / 100 × (máximo − mínimo)
```

| Escala | Mínimo | Máximo |
|---|---|---|
| `ma` | 4 | 20 |
| `pct` | 0 | 100 |
| `v` | 0 | 10 |

**Por qué en porcentaje.** Es la única escala donde la ganancia del modelo es
adimensional y las constantes PID son comparables entre lazos. Si el actuador
estuviera en mA y el sensor en °C, `K` tendría unidades de °C/mA y no se podría
comparar con nada.

Cada muestra guarda **las dos escalas a la vez**: el valor crudo (`actuator`,
`sensor`) y el convertido (`actuator_pct`, `sensor_pct`). Los gráficos muestran
la que elija la vista; **la identificación siempre usa el porcentaje**.

---

## Etapa 2 — Los gráficos

Hay tres gráficos y cada uno se alimenta de una fuente distinta. Confundirlas
es la causa de casi todos los "no se alinea".

### Paso 3 — Señales en tiempo real

| Serie | De dónde sale |
|---|---|
| Actuador (línea sólida) | `sampleStore.actuator_ma` — **lo que el PLC reporta** |
| Actuador (punteada) | `ensayo.plan.actuator` — **lo que el backend comanda** |
| Sensor | `sampleStore.sensor_ma` — lo que el PLC reporta |

La comparación entre la sólida y la punteada es el diagnóstico más útil de la
vista: si la sólida no sigue a la punteada, o la escritura no está armada, o el
actuador saturó, o esa variable la pisa el propio programa del PLC.

Se guardan **separadas** a propósito. Guardar el comando en `actuator_ma`
haría que cambiar la variable mapeada no tuviera ningún efecto visible.

### Paso 4 — Medido vs Modelo

```
eje X      = window.time      (re-basado a 0)
línea verde = window.sensor    (lo medido en la ventana)
línea naranja = model.simulated
```

Las tres tienen **exactamente `window.count` puntos**. Es obligatorio: el buffer
completo dura todo el ensayo y la ventana es solo un recorte, así que mezclarlos
aplasta el modelo contra el arranque del eje.

`window.time` viene **re-basado a 0**, no comparte escala con `sample.time`.

---

## Etapa 3 — Detección del escalón

**Pregunta:** ¿en qué muestra empieza a moverse el actuador?

### El umbral

```
umbral = max( |paso_a_% − paso_desde_%| × 0.5 ,  1.0 )
```

La mitad del salto esperado: suficiente para no dispararse con ruido, y
tolerante si el actuador no llega exactamente al valor final. Con un salto de
15 % → 60 %, el umbral es 22.5 %.

### El algoritmo

Barrido **hacia atrás** manteniendo el máximo que queda por delante:

```
peak = max(actuator[i+1 :])

el primer i (desde el final) que cumple
    peak − actuator[i] ≥ umbral
marca una subida que arranca en o después de i
```

Después se retrocede mientras la señal siga subiendo, hasta el pie de la rampa.
Se devuelve **la primera muestra ya en transición**.

> **Por qué no se compara entre muestras consecutivas.** Un actuador real casi
> nunca salta de golpe: la salida de un PID con rate limiter sube en rampa. Una
> subida de 45 % repartida en 120 muestras da incrementos de 0.375 cada una —
> ninguno supera el umbral. Lo que define el escalón es el cambio **acumulado**.

> **Por qué no hay ventana de ancho fijo.** Una versión anterior comparaba
> dentro de una ventana medida en muestras, topada en 60. A 200 ms eso cubre
> 12 s y sobra; a **20 ms son 1.2 s**, y una rampa de 2.4 s no alcanzaba el
> umbral dentro de la ventana: se reportaba "no se detectó ningún escalón" en un
> registro que claramente tenía uno. El ancho correcto es un **tiempo**, no un
> número de muestras, y depende de una rampa cuya duración no se conoce de
> antemano. Lo mejor es no necesitar ancho.

El barrido va desde el final para quedarse con el escalón **más reciente**: en
un programa que cicla, interesa el último.

---

## Etapa 4 — La ventana de identificación

Recorte alrededor del escalón. No se ajusta sobre el buffer entero.

```
pre_samples  = clamp( retardo_s / periodo ,  5 ,  200 )
post_samples = max( (duración_s − retardo_s) / periodo ,  30 )

inicio = step_index − pre_samples
fin    = step_index + post_samples
```

Con `duración = 120 s`, `retardo = 10 s`, `periodo = 0.2 s`:
`pre = 50`, `post = 550`, ventana de 600 muestras.

**El periodo que se usa es el MEDIDO, no el configurado.** Si la lectura del PLC
va más lenta de lo pedido, traducir la ventana con el periodo teórico pediría
más puntos de los que existen.

### La línea base no cruza la transición anterior

```
paso_previo = última transición antes de step_index
inicio      = max(inicio, paso_previo)
```

En un PLC que cicla (4 mA durante 5 s, 12 mA durante 15 s, en bucle), pedir 10 s
de línea base estiraría la ventana hasta la fase alta del ciclo previo: el
actuador valdría lo mismo al principio y al final, y el identificador concluiría
que no hubo escalón.

Por último, el tiempo se **re-basa a 0** en el inicio de la ventana.

---

## Etapa 5 — La línea base

`detect_step_info` extrae los cuatro números de los que sale todo:

```
initial_u, initial_y  = régimen permanente ANTES del escalón
final_u,   final_y    = último valor de la ventana

Δu = final_u − initial_u
Δy = final_y − initial_y
```

### Cómo se calcula el régimen permanente inicial

```
tramo  = valores[ step_index − max(1, step_index//2) : step_index ]
initial = mediana(tramo)
```

Es decir: **la mediana de la mitad de la línea base más pegada al salto**.

> **Por qué no `sensor[0]`.** Tomar la primera muestra da por hecho que la
> ventana empieza con la planta ya asentada, y muchas veces no es así. Si la
> captura arrancó con el proceso todavía subiendo desde cero hasta su punto de
> operación, la primera muestra no es el estado estable de `initial_u`.
>
> Caso real: sensor arrancando en 0 y subiendo a 20 en los primeros segundos.
> Con `sensor[0] = 0` el modelo simulaba desde 0 mientras lo medido estaba en
> 20, la ganancia absorbía la deriva y el R² salía **negativo**:
>
> | | Con `sensor[0]` | Con la mediana de la base |
> |---|---|---|
> | R² | **−0.055** | **+0.982** |
> | K (real 1.080) | 1.880 | **1.080** |
> | τ (real 3.00) | 2.06 | **3.00** |

> **Por qué mediana y no media.** Un pico de ruido suelto arrastra la media; la
> mediana no se entera.

---

## Etapa 6 — Los tres modelos

La ganancia se calcula igual en los tres:

```
K = Δy / Δu
```

Lo que cambia es cómo se estima la dinámica.

### FOPDT — primer orden con tiempo muerto

```
              K · e^(−L·s)
    G(s) = ───────────────
               τ·s + 1
```

**Método de los dos puntos (Smith).** Se buscan los instantes en que la salida
cruza el 28.3 % y el 63.2 % del recorrido total:

```
τ = 1.5 · (t63 − t28)
L = 1.5 · t28 − 0.5 · t63 − t_escalón
```

Los cruces se **interpolan linealmente** entre muestras, para no quedar atado a
la resolución del muestreo.

> 63.2 % es `1 − 1/e`: en un primer orden, el instante en que la salida alcanza
> ese porcentaje está exactamente a una constante de tiempo del inicio. El
> segundo punto al 28.3 % permite despejar τ y L por separado.

### SOPDT — segundo orden con tiempo muerto

```
                    K · e^(−L·s)
    G(s) = ─────────────────────────────
            (τ₁·s + 1)(τ₂·s + 1)
```

Dos pasos:

1. **Semilla analítica** con los cruces al 35.3 % y 85.3 %:
   ```
   x  = t85 − t35
   τ₁ ≈ 0.6·x       τ₂ ≈ 0.4·x       L ≈ t35 − 0.25·x − t_escalón
   ```

2. **Refinamiento numérico**: Nelder-Mead sobre (K, τ₁, τ₂, L) minimizando el
   error cuadrático contra los datos. Si no mejora la semilla, se queda con la
   semilla.

> Sin el paso 2 el modelo de 2º orden casi siempre perdía el ranking frente al
> FOPDT aunque el proceso fuera realmente de 2º orden. El optimizador es
> Nelder-Mead en Python puro, sin scipy, para que funcione dentro del snap
> del ctrlX.

Por convención `τ₁ ≥ τ₂`: la primera es la constante dominante.

### Integrador con tiempo muerto

```
              K · e^(−L·s)
    G(s) = ───────────────
                  s
```

Procesos que no se estabilizan solos: nivel de un tanque, posición.

```
pendiente = ajuste por mínimos cuadrados sobre el tramo final
K         = pendiente / Δu
```

El tiempo muerto se estima por **intersección de la tangente**: se ajusta la
recta de la rampa y se busca dónde corta el valor que tenía la salida en el
instante del escalón.

> Un umbral porcentual ("cuando se mueva un 5 % del recorrido") **no sirve
> aquí**: en un integrador el recorrido total depende de cuánto dure la ventana,
> así que el mismo proceso daría tiempos muertos distintos según el largo del
> registro.

### La simulación

Los tres simulan **recursivamente** contra el actuador real:

```
y_ss    = y₀ + K · (u(t−L) − u₀)          ← régimen permanente para la entrada actual
y[i+1]  = y_ss + (y[i] − y_ss) · e^(−Δt/τ)
```

> **Por qué recursivo y no la fórmula del escalón.** La ventana incluye muestras
> **antes** del escalón. Con la fórmula cerrada `y = y₀ + KΔu(1 − e^(−t/τ))` el
> exponencial arrancaba en `time[0]` en vez de en el escalón, y la curva
> simulada quedaba desfasada: el R² caía a 0.84 aunque K y τ fueran correctos.
> Además así es O(n) y admite cualquier forma de u(t), no solo un escalón.

---

## Etapa 7 — El orden y el ranking

### Qué significa "orden"

El selector del paso 2 (`order`) decide **qué modelos se ajustan**:

| Valor | Qué hace |
|---|---|
| `auto` | Ajusta los tres y los ordena por R² |
| `fopdt` (1) | Solo primer orden |
| `sopdt` (2) | Solo segundo orden |
| `integrating` (0) | Solo integrador |

### El ranking

```
R² = 1 − SS_res / SS_tot

SS_res = Σ (medido − simulado)²
SS_tot = Σ (medido − media)²
```

Se ordena de mayor a menor R² y el primero es el ganador. La vista lo marca
con el badge **MEJOR**.

> **El R² puede ser negativo.** Significa que el modelo ajusta peor que una
> línea horizontal en la media. **No se recorta a cero a propósito**: el signo
> es información. Un integrador aplicado a un proceso autorregulado da R²
> negativo, y eso es exactamente lo que hay que ver.

### Modelos descartados

Si un modelo no converge, no desaparece: se devuelve en `discarded` con el
motivo y sale como aviso.

> Antes se tragaba con `except: pass` y la tarjeta simplemente no aparecía. No
> había forma de distinguir *"este modelo no aplica a estos datos"* de *"algo se
> rompió"*.

### Un R² alto no significa buen modelo

Si la planta responde más rápido que el muestreo, el transitorio cae entre dos
muestras, τ se colapsa a cero y la función de transferencia degenera en una
**ganancia pura**:

```
G(s) = 0.0500 / ((0.000s+1)(0.000s+1))    ← esos paréntesis valen 1
```

Una constante reproduce perfectamente "plano, plano, salto" — de ahí el R² de
100 %. La señal de alarma es que **FOPDT y SOPDT den el mismo R² y la misma K**:
cuando dos modelos de distinto orden coinciden hasta el último decimal, ninguno
está midiendo dinámica.

---

## Etapa 8 — Sintonía PID

Cuatro métodos, todos a partir de (K, τ, L). Se entregan en **las dos formas**:

- **Estándar** (la del bloque PID de ctrlX): `Kp`, `Ti`, `Td`
- **Paralela**: `Kp`, `Ki`, `Kd`

con `Ki = Kp/Ti` y `Kd = Kp·Td`.

### IMC / Lambda

```
λ  = max(0.25·τ , 0.8·L)
Kp = τ / (K · (λ + L))
Ti = τ                      Td = 0
```

Robusto, para procesos lentos. `λ` es la constante de tiempo de lazo cerrado
deseada: subirla suaviza, bajarla acelera.

### Ziegler-Nichols lazo abierto

```
R  = K / τ                  ← pendiente de reacción
Kp = 1.2 / (R · L)
Ti = 2·L                    Td = 0.5·L
```

Agresivo, buena velocidad de respuesta. Tiende a dejar sobreimpulso.

### Cohen-Coon

```
r  = L / τ
Kp = (1.35 / (K·r)) · (1 + 0.18·r / (1 + 0.185·r))
Ti = L · (2.5 − 2·r) / (1 − 0.39·r)      si r < 1
Td = 0.37·L / (1 + 0.185·r)
```

Balance entre rapidez y estabilidad. Pensado para procesos con tiempo muerto
apreciable.

### SIMC (Skogestad)

```
tc = L                      ← constante de lazo cerrado
Kp = τ / (K · (tc + L))  =  τ / (2·K·L)
Ti = min(τ , 4·(tc + L))    Td = 0
```

Excelente rechazo de perturbaciones. Suele ser el punto de partida más sensato.

### SOPDT → FOPDT: media-regla de Skogestad

Para sintonizar un modelo de segundo orden se reduce a uno equivalente de
primero repartiendo la constante rápida:

```
τ_eq = τ_lenta + τ_rápida/2
L_eq = L + τ_rápida/2
```

Los métodos aparecen entonces con el sufijo **(SOPDT eq.)**.

### Procesos integradores

```
tc = L
Kp = 1 / (K · (tc + L))      Ti = 4·(tc + L)     ← SIMC
```

más una variante IMC más conservadora.

---

## Los avisos: cuándo no fiarse

La barra naranja del paso 4 no es decorativa. Cada aviso apunta a una razón
concreta para desconfiar del ajuste.

| Aviso | Qué significa | Qué hacer |
|---|---|---|
| **El sensor todavía se movía antes del escalón** | La planta no estaba en reposo: el supuesto de partida es falso | Dejar asentar el proceso, o alargar el retardo del paso 2 |
| **El muestreo real es N× el configurado** | La lectura del PLC no alcanza el ritmo pedido | Bajar el tiempo de muestreo a un valor alcanzable, o alargar el ensayo |
| **La constante de tiempo salió prácticamente nula** | La respuesta se completó entre dos muestras | Muestrear más rápido. Solo la ganancia es confiable |
| **tau menor que 3 periodos de muestreo** | Hay muy pocos puntos en el transitorio | Muestrear más rápido |
| **R² negativo** | El modelo ajusta peor que una recta | No usar para sintonizar. Revisar la línea base |
| **Identificado con N muestras, se pedían M** | La ventana quedó más corta que la duración configurada | Verificar que la curva llegó al nuevo estable |
| **MODELO: no se pudo ajustar** | Ese modelo no convergió, con el motivo | Normalmente el modelo no aplica a esos datos |

---

## Resumen en una línea por etapa

1. **Captura** — 1 petición por muestra, todo a % de span, dos escalas guardadas.
2. **Gráficos** — comandado y leído separados; medido y modelo sobre la misma ventana.
3. **Escalón** — cambio acumulado, no pendiente instantánea; sin ancho de ventana.
4. **Ventana** — recorte con el periodo medido; la base no cruza la transición previa.
5. **Línea base** — mediana del tramo pegado al salto, no la primera muestra.
6. **Modelos** — dos puntos (FOPDT), semilla + Nelder-Mead (SOPDT), mínimos cuadrados (integrador).
7. **Orden** — `auto` compite los tres y ordena por R²; el R² negativo se conserva.
8. **PID** — IMC, ZN, Cohen-Coon y SIMC, en forma estándar y paralela.
