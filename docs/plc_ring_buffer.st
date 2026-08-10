(*
    ===========================================================================
    Búfer circular para captura sin pérdida — ctrlX / CODESYS
    ===========================================================================

    POR QUÉ ESTE BLOQUE

    Leer las variables una por una desde la aplicación NO garantiza capturar
    todas las muestras. La tarea IEC corre con su reloj y el lector OPC UA
    sondea con otro: sin sincronización entre ambos se lee dos veces el mismo
    ciclo y se pierde otro. Muestrear más rápido baja la probabilidad, no la
    elimina.

    Aquí el muestreo lo hace la propia tarea. Cada ciclo escribe una posición
    del array, así que el periodo es exactamente el de la tarea (20 ms), sin
    jitter y sin huecos. La aplicación lee el array completo en UN viaje de red
    cada pocos segundos.

    ---------------------------------------------------------------------------
    CÓMO USARLO

    1. Pegar estas declaraciones y el cuerpo en el PLC_PRG (o en un POU nuevo
       llamado desde la tarea que corre a 20 ms).

    2. Publicar en el Datalayer / OPC UA estas variables:
           arrActuator, arrSensor, arrSetPoint, arrTimeSec
           nWriteCount, rTaskPeriodSec
       En "Configuración de símbolos" hay que marcarlas con acceso de lectura.

    3. En la aplicación, activar el modo bloque e indicar esos nombres.

    ---------------------------------------------------------------------------
    DIMENSIONADO

    BUFFER_SIZE debe cubrir CON HOLGURA el intervalo entre lecturas:

        segundos_de_buffer = BUFFER_SIZE * periodo_de_tarea

    Con 2000 muestras a 20 ms  ->  40 s de historia.
    La app lee cada ~20 s (la mitad), así que tolera un hipo de red de 20 s
    antes de perder nada. Subir BUFFER_SIZE cuesta memoria, nada más:
    4 arrays * 2000 * 4-8 bytes ~ 50 KB.

    ---------------------------------------------------------------------------
    DETECCIÓN DE PÉRDIDA

    nWriteCount es un contador ABSOLUTO de muestras escritas desde el arranque,
    no un índice. Eso permite a la app distinguir "no pasó nada" de "dio una
    vuelta entera y volvió al mismo índice", y calcular exactamente cuántas
    muestras se sobrescribieron si llegó tarde. La app reporta ese caso como
    overrun en vez de entregar una serie con huecos invisibles: una serie a la
    que le faltan tramos en silencio produce un modelo que parece válido.
*)


(* ========================= DECLARACIONES ========================= *)

VAR CONSTANT
    BUFFER_SIZE : DINT := 2000;   (* 40 s a 20 ms *)
END_VAR

VAR
    (* --- Búfer circular: un array por señal --- *)
    arrActuator   : ARRAY[0..1999] OF REAL;
    arrSensor     : ARRAY[0..1999] OF REAL;
    arrSetPoint   : ARRAY[0..1999] OF REAL;
    arrTimeSec    : ARRAY[0..1999] OF LREAL;

    (* --- Control ---
       nWriteCount es absoluto y NO se reinicia con la vuelta del array.
       Es lo que permite detectar pérdida. LINT para que no desborde:
       a 20 ms, un DINT se agotaría en ~1.5 años, un LINT nunca. *)
    nWriteCount   : LINT := 0;

    (* Periodo real de la tarea, en segundos. La app lo usa para reconstruir
       el eje de tiempo si arrTimeSec no estuviera publicado. *)
    rTaskPeriodSec : LREAL := 0.02;

    (* Reloj monótono del proceso. NO se reinicia nunca: un contador que
       vuelve a cero cada N segundos produce un eje en diente de sierra y
       arruina la integración del modelo. *)
    rTimeSec      : LREAL := 0.0;

    (* Índice de escritura, derivado de nWriteCount. Interno. *)
    nIdx          : DINT := 0;

    (* --- Señales de proceso (las que ya tenías) --- *)
    rActuator     : REAL;
    rSensor       : REAL;
    rSetPoint     : REAL;
END_VAR


(* ============================= CUERPO ============================= *)

(* 1. Reloj monótono. Avanza siempre; nunca se reinicia. *)
rTimeSec := rTimeSec + rTaskPeriodSec;

(* 2. Posición a escribir en esta vuelta. *)
nIdx := DINT#(nWriteCount MOD LINT#(BUFFER_SIZE));

(* 3. Instantánea de todas las señales EN EL MISMO ciclo.
      Es importante que sea el mismo: si una señal se guardara un ciclo
      después que otra, el modelo vería un desfase artificial de 20 ms
      entre entrada y salida y lo interpretaría como tiempo muerto. *)
arrTimeSec[nIdx]  := rTimeSec;
arrActuator[nIdx] := rActuator;
arrSensor[nIdx]   := rSensor;
arrSetPoint[nIdx] := rSetPoint;

(* 4. Recién DESPUÉS de escribir se publica el contador.
      Al revés, la app podría leer un índice que todavía no tiene dato. *)
nWriteCount := nWriteCount + 1;


(*
    ---------------------------------------------------------------------------
    NOTA SOBRE EL ACTUADOR DURANTE EL ENSAYO

    Si la aplicación va a comandar el escalón, rActuator tiene que ser una
    variable que el programa PLC NO sobrescriba en el mismo ciclo. Si el
    programa la recalcula después de que la app la escribió, el escalón nunca
    llega a la planta y el ensayo registra una entrada que no ocurrió.

    Lo habitual es exponer una variable de comando aparte:

        VAR
            rActuatorCmd : REAL;   (* la escribe la app *)
            bRemoteMode  : BOOL;   (* la activa la app durante el ensayo *)
        END_VAR

        IF bRemoteMode THEN
            rActuator := rActuatorCmd;
        ELSE
            rActuator := (* lo que calcule normalmente el programa *);
        END_IF
*)
