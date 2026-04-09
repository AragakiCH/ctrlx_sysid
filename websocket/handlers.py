from __future__ import annotations


async def handle_ws_message(
    message: dict,
    realtime_service,
    manager,
    websocket,
    latest_identification_result=None,
) -> None:
    msg_type = message.get("type")

    if msg_type == "ping":
        await manager.send_json(websocket, {"type": "pong"})
        return

    if msg_type == "get_latest":
        latest = realtime_service.get_latest_sample()
        await manager.send_json(
            websocket,
            {
                "type": "latest",
                "data": latest,
            },
        )
        return

    if msg_type == "get_series":
        series = realtime_service.get_series_payload()
        await manager.send_json(
            websocket,
            {
                "type": "series",
                "data": series,
            },
        )
        return

    if msg_type == "get_latest_identification":
        await manager.send_json(
            websocket,
            {
                "type": "identification_result",
                "data": latest_identification_result,
            },
        )
        return

    if msg_type == "clear_buffer":
        realtime_service.clear()
        await manager.send_json(
            websocket,
            {
                "type": "buffer_cleared",
            },
        )
        return

    await manager.send_json(
        websocket,
        {
            "type": "error",
            "message": f"Mensaje no soportado: {msg_type}",
        },
    )