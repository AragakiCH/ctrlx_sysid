from __future__ import annotations

from domain.models.signals import SignalSeries


class StepDetectorService:
    def __init__(self, min_step_delta: float = 1.0) -> None:
        self.min_step_delta = min_step_delta

    def find_latest_rising_step_index(
        self, actuator_data: list[float], window: int | None = None
    ) -> int | None:
        """
        Busca la última subida suficientemente grande y devuelve el índice
        donde ARRANCA.

        Un actuador real casi nunca salta de golpe: la salida de un PID con
        rate limiter sube en rampa. Comparar muestras consecutivas deja pasar
        una subida de 45 % repartida en cientos de muestras, así que lo que
        define el escalón es el cambio ACUMULADO.

        El barrido va hacia atrás manteniendo el máximo que queda por delante.
        No necesita ancho de ventana, y ahí está la diferencia: la versión
        anterior usaba una ventana deslizante medida en MUESTRAS, topada en 60.
        A 200 ms eso cubre 12 s y sobra, pero a 20 ms son 1.2 s: una rampa de
        2.4 s no alcanzaba el umbral dentro de la ventana y el escalón se
        declaraba inexistente aunque el actuador se hubiera movido el salto
        completo. El ancho correcto es un TIEMPO, no un número de muestras, y
        depende de una rampa cuya duración no se conoce de antemano — así que
        lo mejor es no necesitar ancho.

        Devuelve el pie de la rampa porque el tiempo muerto del proceso se mide
        desde que el actuador EMPIEZA a moverse.

        `window` se conserva en la firma por compatibilidad; ya no se usa.
        """
        n = len(actuator_data)
        if n < 2:
            return None

        # `peak` es el máximo de actuator_data[i+1:]. Que `peak - valor[i]`
        # supere el umbral significa que a partir de i el actuador sube al
        # menos ese salto, sin importar cuánto tarde en hacerlo.
        peak = actuator_data[n - 1]
        encontrado = None

        for i in range(n - 2, -1, -1):
            if peak - actuator_data[i] >= self.min_step_delta:
                encontrado = i
                break
            if actuator_data[i] > peak:
                peak = actuator_data[i]

        if encontrado is None:
            return None

        # `encontrado` es el último punto que queda `min_step_delta` por debajo
        # del máximo posterior: en una rampa eso cae a media subida, no en el
        # pie. Se retrocede mientras la señal siga subiendo hacia adelante.
        #
        # La comparación es estricta (`>`): en la línea base la diferencia
        # entre muestras es cero, así que el recorrido se detiene solo y no se
        # come el tramo plano previo.
        rise_eps = max(self.min_step_delta * 1e-4, 1e-9)

        j = encontrado
        while j > 0 and actuator_data[j] - actuator_data[j - 1] > rise_eps:
            j -= 1

        # El bucle deja `j` en la última muestra de la línea base. Se devuelve
        # la siguiente: es la primera ya en transición, que es la convención
        # que usa `SignalProcessor.detect_step_info` para `step_index`. Tenerlas
        # desfasadas una muestra descuadraría `step_time` y, con él, el tiempo
        # muerto de todos los modelos.
        return min(j + 1, n - 1)

    def find_previous_step_index(
        self, actuator_data: list[float], before_index: int
    ) -> int | None:
        """
        Busca el escalón anterior a `before_index`, de cualquier signo.

        Se usa para no dejar que la línea base se estire hacia atrás más allá de
        la transición previa. En un programa de PLC que cicla (por ejemplo 4 mA
        durante 5 s y 12 mA durante 15 s, en bucle), pedir 10 s de línea base
        haría que la ventana empezara en la fase alta del ciclo anterior: el
        actuador valdría lo mismo al principio y al final de la ventana, y el
        identificador concluiría que no hubo escalón.
        """
        if before_index <= 0:
            return None

        for i in range(before_index - 1, 0, -1):
            if abs(actuator_data[i] - actuator_data[i - 1]) >= self.min_step_delta:
                return i

        return None

    def find_next_transition_index(
        self, actuator_data: list[float], after_index: int
    ) -> int | None:
        """
        Primer punto, después de `after_index`, en el que el actuador vuelve a
        BAJAR al menos `min_step_delta` respecto al máximo que alcanzó tras el
        escalón. Ahí termina la respuesta al escalón que se está identificando.

        Es el espejo de `find_previous_step_index` para el lado derecho de la
        ventana, y existe por el ensayo con escritura al PLC: al terminar, el
        runner devuelve el actuador a `step_from`. La ventana se dimensiona con
        `post_samples` contados desde el escalón, pero el escalón se LEE una
        muestra después de escribirse (el PLC lo aplica y OPC UA lo publica en
        la lectura siguiente), así que la ventana quedaba una muestra más larga
        que el ensayo y su última muestra ya traía el actuador de vuelta al
        valor inicial. `detect_step_info` toma `final_u` de esa última muestra:
        `final_u == initial_u`, `delta_u == 0` y el ajuste se rechazaba con
        "No se detectó cambio en el actuador" aunque el gráfico mostrara el
        escalón entero.

        Se compara contra el máximo acumulado, no entre muestras consecutivas,
        para que una vuelta en rampa (rate limiter en el PLC) también se
        detecte. Durante la propia subida el máximo acumulado es el valor
        actual, así que la rampa de subida nunca dispara el corte.
        """
        n = len(actuator_data)
        if after_index < 0 or after_index >= n - 1:
            return None

        peak = actuator_data[after_index]

        for k in range(after_index + 1, n):
            valor = actuator_data[k]
            if valor > peak:
                peak = valor
                continue
            if peak - valor >= self.min_step_delta:
                return k

        return None

    def find_latest_step_index(self, actuator_data: list[float]) -> int | None:
        """
        Busca el último escalón de cualquier signo.
        """
        if len(actuator_data) < 2:
            return None

        for i in range(len(actuator_data) - 1, 0, -1):
            delta = abs(actuator_data[i] - actuator_data[i - 1])
            if delta >= self.min_step_delta:
                return i

        return None

    def extract_window_from_step_index(
        self,
        series: SignalSeries,
        step_index: int,
        pre_samples: int = 10,
        post_samples: int = 40,
    ) -> SignalSeries | None:
        if step_index < 0 or step_index >= len(series.time):
            return None

        start = max(0, step_index - pre_samples)

        # La línea base no puede cruzar la transición anterior: si lo hiciera,
        # la ventana contendría dos escalones y el ajuste no tendría sentido.
        previous_step = self.find_previous_step_index(series.actuator, step_index)
        if previous_step is not None:
            start = max(start, previous_step)

        end = min(len(series.time), step_index + post_samples)

        # Y tampoco puede cruzar la transición SIGUIENTE: si el actuador ya
        # volvió al valor inicial (fin del ensayo con escritura al PLC, o un
        # programa que cicla), lo que sigue no es respuesta a este escalón.
        next_transition = self.find_next_transition_index(series.actuator, step_index)
        if next_transition is not None:
            end = min(end, next_transition)

        if end - start < 20:
            return None

        window_time = series.time[start:end]
        t0 = window_time[0]
        window_time = [t - t0 for t in window_time]

        return SignalSeries(
            time=window_time,
            actuator=series.actuator[start:end],
            sensor=series.sensor[start:end],
            setpoint=series.setpoint[start:end] if series.setpoint else [],
            signal_type=series.signal_type,
        )