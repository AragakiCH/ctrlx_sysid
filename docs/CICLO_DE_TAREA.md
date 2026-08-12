# Vincular el tiempo de muestreo con el ciclo del MainTask

## Lo primero: por OPC UA el ciclo de tarea no existe

El campo **Intervalo** del `MainTask` que se ve en ctrlX PLC Engineering vive en
la **Configuración de tareas** del proyecto. Es metadato del proyecto compilado,
no una variable del programa:

- no se declara en `PLC_PRG`,
- no se exporta en la **Configuración de símbolos**,
- por tanto no cuelga de `Objects → Datalayer → plc → app → Application → sym`,
- y no hay ningún NodeId al que hacerle un write.

No es una limitación de esta app ni del cliente OPC UA. Buscar ese nodo es
buscar algo que el servidor no publica.

## Lo que sí funciona: una variable puente

El intervalo se puede cambiar **desde dentro del PLC**. Entonces el reparto es:

```
Vista  ──write OPC UA──►  uiTaskCycleMs (variable de PLC_PRG)
                                │
                                │  la lee el propio programa
                                ▼
                          código IEC  ──►  intervalo del MainTask
```

La app escribe una variable normal y corriente. El cambio de tarea lo hace el
PLC. Eso es todo lo que hay que montar.

---

## Paso 1 — Declarar la variable en `PLC_PRG`

```pascal
VAR
    // Ciclo de tarea pedido desde la aplicación web, en ms.
    // La app OPC UA escribe aquí; el programa lo aplica al MainTask.
    uiTaskCycleMs      : UINT := 20;

    // Último valor aplicado. Sirve para detectar el flanco de cambio y para
    // que la app pueda leer el ciclo REAL, no el pedido.
    uiTaskCycleApplied : UINT := 0;
END_VAR
```

## Paso 2 — Exponerla en la Configuración de símbolos

En **Configuración de símbolos**, marcar `uiTaskCycleMs` con acceso de
**lectura y escritura**. Si queda como solo lectura, el ctrlX rechaza el write y
la app lo dirá en la vista (`writable: false`).

`uiTaskCycleApplied` basta con exponerla de lectura.

## Paso 3 — Aplicar el cambio desde IEC

> **Verificar contra tu versión de runtime.** El acceso al descriptor de tarea
> cambió entre versiones de CODESYS y ctrlX. Compilá esto en tu proyecto y
> comprobá que `IecTaskGetCurrent` resuelve antes de descargarlo al PLC. Si tu
> runtime no expone `CmpIecTask`, esta parte no es posible y hay que quedarse
> con la lectura (paso 4), que ya evita el problema real.

Agregar la librería **`CmpIecTask`** en el Administrador de bibliotecas, y en
`PLC_PRG`:

```pascal
VAR
    pTaskInfo : POINTER TO CmpIecTask.Task_Info2;
END_VAR

// Solo al cambiar: escribir el descriptor en cada ciclo es tocar el
// planificador 50 veces por segundo sin ninguna necesidad.
IF uiTaskCycleMs <> uiTaskCycleApplied THEN

    // Barrera propia del PLC. La app ya valida 5..200 ms, pero un write
    // OPC UA puede venir de cualquier cliente, no solo de esta aplicación.
    IF uiTaskCycleMs >= 5 AND uiTaskCycleMs <= 200 THEN

        pTaskInfo := CmpIecTask.IecTaskGetCurrent();

        IF pTaskInfo <> 0 THEN
            // dwCycleTime está en MICROsegundos.
            pTaskInfo^.dwCycleTime := UINT_TO_UDINT(uiTaskCycleMs) * 1000;
            uiTaskCycleApplied     := uiTaskCycleMs;
        END_IF

    ELSE
        // Fuera de rango: se rechaza y se devuelve el valor vigente para que
        // la app vea que no se aplicó.
        uiTaskCycleMs := uiTaskCycleApplied;
    END_IF

END_IF
```

## Paso 4 — Configurarlo en la vista

En el paso 1, bajo *Tiempo de muestreo*:

1. Elegir `uiTaskCycleMs` en **Variable del ciclo de tarea**.
2. Marcar **Bajar el ciclo del PLC si el muestreo lo exige** si se quiere que el
   ajuste sea automático.

La app muestra el ciclo leído y avisa cuando el muestreo pedido queda por
debajo.

---

## Cómo verificar que el ciclo cambió de verdad

**El campo `Intervalo` de la Configuración de tareas NO va a cambiar nunca.**
Ese campo es el valor del **proyecto**, guardado en el `.project`. Escribir
`dwCycleTime` modifica el descriptor de tarea del **runtime**, en memoria, y no
se propaga de vuelta al proyecto. Va a seguir diciendo `20` aunque la tarea esté
corriendo a 10 ms. Mirar ahí no sirve para comprobar nada.

Hay tres formas que sí sirven, de menos a más fiable:

### 1. El monitor de tareas (sin tocar código)

Estando **en línea**, en el árbol: `Configuración de tareas` → pestaña
**Supervisar**. Muestra el ciclo real, mínimo, máximo y medio de cada tarea. Es
el runtime hablando, no el proyecto.

### 2. La variable de confirmación

`uiTaskCycleApplied` solo se escribe cuando el cambio se aplicó. Si pediste
10 ms y sigue en 20, el `IF` no entró: o el valor quedó fuera del rango, o
`IecTaskGetCurrent()` devolvió 0.

### 3. Medirlo (la única prueba independiente)

Las dos anteriores dependen de que la API de tarea haga lo que se espera. Esto
no depende de nada: cuenta ciclos contra el reloj del sistema.

```pascal
VAR
    tUltimaMedida  : TIME;
    uiCiclos       : UINT := 0;
    rCicloRealMs   : REAL := 0.0;   // exponer en la Configuración de símbolos
END_VAR

uiCiclos := uiCiclos + 1;

// Cada segundo se convierte la cuenta en un periodo medio.
IF TIME_TO_UDINT(TIME() - tUltimaMedida) >= 1000 THEN
    IF uiCiclos > 0 THEN
        rCicloRealMs := 1000.0 / UINT_TO_REAL(uiCiclos);
    END_IF
    uiCiclos      := 0;
    tUltimaMedida := TIME();
END_IF
```

Con la tarea a 20 ms, `rCicloRealMs` marca ≈ 20. Si cambia a ≈ 10 tras pedir
10 ms, el cambio funcionó. Si se queda en 20, no.

Se puede mapear `rCicloRealMs` como **Variable del ciclo de tarea** en la vista
en lugar de `uiTaskCycleMs`, y así la app muestra el ciclo medido. Para escribir
hace falta la variable de comando; para solo verificar, esta es mejor.

## Por qué el ajuste es solo hacia abajo

Son dos cosas distintas:

| | Qué es |
|---|---|
| **Ciclo de tarea** | cada cuánto el PLC **calcula** el proceso |
| **Tiempo de muestreo** | cada cuánto la app **lo mira** |

Mirar más despacio de lo que el PLC calcula es perfectamente normal y no cuesta
nada: muestrear a 500 ms con la tarea en 20 ms da una curva más gruesa pero
correcta.

Mirar **más rápido** sí es un problema: por debajo del ciclo la variable todavía
no cambió, así que llegan muestras repetidas. La curva sale escalonada y la
identificación lee mal el tiempo muerto y la constante de tiempo.

Por eso la sincronización solo **baja** el ciclo, nunca lo sube. Frenar el PLC
porque la app decidió mirar despacio degradaría el control real sin aportar
nada a la identificación.

## Los límites, y por qué existen

| | Valor | Motivo |
|---|---|---|
| `MIN_CYCLE_MS` | 5 ms | Bajar el ciclo le da al PLC menos tiempo para terminar su trabajo. Si no llega, el **watchdog lo manda a STOP**. Con el watchdog del proyecto en 100 ms y sensibilidad 4, un ciclo de 1 ms es una forma realista de parar una planta desde un navegador. |
| `MAX_CYCLE_MS` | 200 ms | Más lento que esto ya no es muestrear despacio, es frenar el proceso. Para muestrear más despacio no hace falta tocar el PLC. |

Ambos están en `application/services/task_cycle_service.py`.

---

## Antes de usarlo: comprobá qué hace tu `FB_Proceso2doOrden_1`

Esto decide si cambiar el ciclo es inocuo o si te cambia la planta bajo los pies.

Un bloque de simulación de proceso integra de una de dos formas:

**a) Con `dt` explícito** — recibe el paso de integración como entrada:

```pascal
FB_Proceso2doOrden_1(rInput := ..., rDt := 0.02, ...);
```

Cambiar el ciclo de tarea **no altera** la dinámica simulada: τ y K siguen
siendo los mismos. Salvo que `rDt` esté cableado a una constante que no se
actualice con el ciclo — en ese caso hay que alimentarlo con
`UINT_TO_REAL(uiTaskCycleApplied) / 1000.0`.

**b) Asumiendo el ciclo** — integra una vez por llamada sin `dt`:

```pascal
rY := rY + (rU - rY) * rK;   // rK calibrado para un ciclo fijo
```

Aquí cambiar el ciclo **cambia la planta**: a 10 ms el proceso simulado
responde el doble de rápido que a 20 ms. Identificarías un sistema distinto
cada vez que tocaras el tiempo de muestreo, y las constantes PID resultantes no
servirían para el proceso real.

### Cómo saberlo sin leer el código

Un ensayo lo resuelve:

1. Escalón con la tarea a **20 ms**. Anotar τ.
2. Escalón con la tarea a **40 ms**. Anotar τ.

| Resultado | Qué significa |
|---|---|
| τ igual en los dos | El FB usa `dt`. Cambiar el ciclo es seguro. |
| τ se duplica al pasar a 40 ms | El FB asume el ciclo. **No** conviene vincularlo: dejá la casilla desmarcada y usá la app solo para leer el ciclo y avisar del sobremuestreo. |

---

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/api/plc/task-cycle` | Ciclo leído, si es escribible, si hay sobremuestreo |
| `POST` | `/api/plc/task-cycle/config` | Fija la variable puente y el interruptor |
| `POST` | `/api/plc/task-cycle` | Escribe un ciclo concreto, con los límites aplicados |

Ejemplo de respuesta:

```json
{
  "variable": "uiTaskCycleMs",
  "sync_enabled": true,
  "cycle_ms": 20.0,
  "writable": true,
  "reason": null,
  "oversampling": false,
  "min_cycle_ms": 5.0,
  "max_cycle_ms": 200.0
}
```

Sin variable configurada todo queda inactivo y la app funciona igual: es una
mejora opcional, no un requisito.
