"""
Adds support for the Zibo 737 in X-Plane

Many X-Plane aircraft have similar formats for datarefs and the means of retrieving, translating and sending updates is mostly the same.

In order to support multiple CDU devices seamlessly, a dynamic approach is taken whereby an enum class is defined that contains the supported devices.
A device is considered "supported" if it exists in the aircraft. Some aircraft have 3 CDUs while others have 2.
Each enum member is assigned a value that represents the X-Plane dataref identifier. Example: fmc1 of laminar/B738/fmc1/Line04_I.

Upon script start, MobiFlight is probed (get_available_devices()) to detect the devices connected to the PC. Any device that returns a successful response is then tracked.

Two tasks are started independently for each avialable CDU device.
1. handle_dataref_updates -> Listens to X-Plane's WebSocket server for dataref updates for that specific CDU and pushes an event to a queue
2. handle_device_update   -> Listens to the queue and dispatches updates to MobiFlight to update that CDU

Tasks are started independently for each CDU device to ensure each device can update quickly, particularly when players might be performing shared cockpit flights.

Upon a failed connection while dispatching updates to MobiFlight, the handle_device_update function use `async for` with the websockets client. The failed message is put back in the queue, the loop continues to the next iteration which then reconnects again.
The failed message is picked back up and dispatched to MobiFlight. This ensures a user's device eventually receives the updated display contents and doesn't hang which would require the user to cycle the page again.
"""

import asyncio
import base64
import json
import logging
import urllib.request
import websockets
from enum import StrEnum

CDU_COLUMNS = 24
CDU_ROWS = 14

WEBSOCKET_HOST = "localhost"
WEBSOCKET_PORT = 8320

BASE_REST_URL = "http://localhost:8086/api/v2/datarefs"
BASE_WEBSOCKET_URI = f"ws://{WEBSOCKET_HOST}:8086/api/v2"

WS_CAPTAIN = f"ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT}/winwing/cdu-captain"
WS_CO_PILOT = f"ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT}/winwing/cdu-co-pilot"

BALLOT_BOX = "☐"
UP_ARROW = "↑"
DOWN_ARROW = "↓"
LEFT_ARROW = "←"
RIGHT_ARROW = "→"
GREEK_DELTA = "Δ"
DEGREES = "°"

CONTENT_MAP = {"`": DEGREES, "|": GREEK_DELTA}

SYMBOL_MAP = CONTENT_MAP | {
    "A": "[",
    "B": "]",
    "E": BALLOT_BOX,
    "0": LEFT_ARROW,
    "1": RIGHT_ARROW,
    "2": LEFT_ARROW,
    "3": RIGHT_ARROW,
    "4": LEFT_ARROW,
    "5": RIGHT_ARROW,
    "C": UP_ARROW,
    "D": DOWN_ARROW,
}

COLOR_MAP = {"b": "c"}

SYMBOL_COLOR_MAP = {"E": "a", "4": "a", "5": "a"}


class CduDevice(StrEnum):
    Captain = "MCDU1"
    CoPilot = "MCDU2"

    def get_endpoint(self) -> str:
        match self:
            case CduDevice.Captain:
                return WS_CAPTAIN
            case CduDevice.CoPilot:
                return WS_CO_PILOT
            case _:
                raise KeyError(f"Invalid device specified {self}")


def get_color(dataref_name: str, char: str):
    suffix = dataref_name[-1]

    if (
        ("label" in dataref_name or "title" in dataref_name) and suffix == "s"
    ) or dataref_name.endswith("VertSlewKeys"):
        return "w"
    elif suffix == "s":
        return SYMBOL_COLOR_MAP.get(char, "c")

    return COLOR_MAP.get(suffix, suffix)


def get_char(dataref_name: str, char: str) -> str:
    suffix = dataref_name[-1]

    if suffix == "s":
        return SYMBOL_MAP.get(char, char)

    return CONTENT_MAP.get(char, char)


def get_size(dataref_name):
    return (
        1
        if "scont" in dataref_name
        or ("label" in dataref_name and "labelL" not in dataref_name)
        else 0
    )


def fetch_dataref_mapping(device: CduDevice):
    with urllib.request.urlopen(BASE_REST_URL, timeout=5) as response:
        response_json = json.load(response)

        data = list(response_json["data"])
        mcdu_datarefs = filter(
            lambda x: str(x["name"]).startswith(f"AirbusFBW/{device}"), data
        )

        return dict(
            map(
                lambda dataref: (int(dataref["id"]), str(dataref["name"])),
                mcdu_datarefs,
            )
        )


def process_cdu_line(line_datarefs: dict[str, str], row: int) -> list[list]:
    line_chars = [[] for _ in range(CDU_COLUMNS)]

    target_suffix = "label" if row % 2 == 0 else "cont"

    for dataref, text in line_datarefs.items():
        if not text or text.isspace():
            continue

        # The first and last rows only cover a single row. All other rows cover 2 rows between the label and the main content (scont, cont)
        if (row != 0 and row != 14) and not target_suffix in dataref:
            continue

        for i, char in enumerate(text[:CDU_COLUMNS]):
            if char == " ":
                continue

            line_chars[i] = (
                get_char(dataref, char),
                get_color(dataref, char),
                get_size(dataref),
            )

    return line_chars


def group_datarefs_by_line(values: dict[str, str]) -> dict[int, dict[str, str]]:
    grouped_datarefs = {}

    for dataref, value in values.items():
        try:
            line_num = (
                0
                if "title" in dataref
                else (
                    7
                    if dataref.endswith("spa")
                    or dataref.endswith("spw")
                    or dataref.endswith("VertSlewKeys")
                    else int(next(i for i in list(dataref[::-1]) if i.isdigit()))
                )
            )
            if line_num not in grouped_datarefs:
                grouped_datarefs[line_num] = {}

            grouped_datarefs[line_num][dataref] = value
        except Exception as e:
            pass

    return grouped_datarefs


def generate_display_json(values: dict[str, str]):
    display_data = [[] for _ in range(CDU_ROWS * CDU_COLUMNS)]

    grouped_datarefs = group_datarefs_by_line(values)

    for row in range(CDU_ROWS + 1):
        if (
            row == 1
        ):  # Line 0 covers a single row. Skip row 1 as this will be populated by Line01
            continue

        dataref_line = row // 2 if row > 0 else 0

        line_datarefs = grouped_datarefs.get(dataref_line, {})
        if not line_datarefs:
            continue

        line_chars = process_cdu_line(line_datarefs, row)

        start_index = (row - 1 if row > 0 else row) * CDU_COLUMNS
        display_data[start_index : start_index + CDU_COLUMNS] = (
            line_chars  # Bulk assign the entire row
        )

    return json.dumps({"Target": "Display", "Data": display_data})


async def handle_device_update(queue: asyncio.Queue, device: CduDevice):
    """
    Translates and sends dataref updates to MobiFlight.
    """
    last_run_time = 0
    rate_limit_time = 0.1

    endpoint = device.get_endpoint()
    logging.info("Connecting to CDU device %s", device)
    async for websocket in websockets.connect(endpoint):
        logging.info("Connected successfully to CDU device %s", device)
        while True:
            values = await queue.get()

            try:
                elapsed = asyncio.get_event_loop().time() - last_run_time

                # Weaker CPUs may experience performance issues when a websocket connection is saturated with requests, such as when pages are frequently changed.
                # This rate limits the number of active websocket requests to MobiFlight.
                # The delay should not be noticeable unless a user heavily spams page changes, but it should be enough that too many messages won't be pushed at once.
                if elapsed < rate_limit_time:
                    await asyncio.sleep(rate_limit_time - elapsed)

                display_json = generate_display_json(values)
                await websocket.send(display_json)
                last_run_time = asyncio.get_event_loop().time()

            except websockets.exceptions.ConnectionClosed:
                logging.error(
                    "MobiFlight websocket connection was closed... Attempting to reconnect"
                )
                await queue.put(values)
                break


async def handle_dataref_updates(queue: asyncio.Queue, device: CduDevice):
    def process_slew_keys(value: int) -> str:
        match value:
            case 1:
                result = f"{UP_ARROW}{DOWN_ARROW}"
            case 2:
                result = f"{UP_ARROW} "
            case 3:
                result = f"{DOWN_ARROW}"
            case _:
                result = ""

        return result.rjust(24)

    last_known_values = {}

    dataref_map = fetch_dataref_mapping(device)
    logging.info("Connecting to X-Plane websocket server")
    async for websocket in websockets.connect(BASE_WEBSOCKET_URI):
        logging.info("Connected successfully to X-Plane websocket server")
        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "dataref_subscribe_values",
                        "req_id": 1,
                        "params": {
                            "datarefs": [
                                {"id": id_value} for id_value in dataref_map.keys()
                            ]
                        },
                    }
                )
            )
            while True:
                message = await websocket.recv()
                data = json.loads(message)

                if "data" not in data:
                    continue

                new_values = dict(last_known_values)

                for dataref_id, value in data["data"].items():
                    dataref_id = int(dataref_id)
                    if dataref_id not in dataref_map:
                        continue

                    dataref_name = dataref_map[dataref_id]

                    if dataref_name.endswith("VertSlewKeys"):
                        new_values[dataref_name] = process_slew_keys(value)
                    else:
                        new_values[dataref_name] = (
                            base64.b64decode(value).decode().replace("\x00", " ")
                        )

                if new_values == last_known_values:
                    continue

                last_known_values = new_values
                await queue.put(new_values)
        except websockets.exceptions.ConnectionClosed:
            logging.error(
                "X-Plane websocket connection was closed... Attempting to reconnect"
            )
            continue


async def get_available_devices() -> list[CduDevice]:
    device_candidates = [device for device in CduDevice]

    available_devices = []

    logging.info("Checking MobiFlight for available CDU devices")
    for device in device_candidates:
        device_endpoint = device.get_endpoint()
        try:
            async with websockets.connect(device_endpoint) as _:
                logging.info(
                    "Discovered CDU device %s at endpoint %s", device, device_endpoint
                )
                available_devices.append(device)
        except websockets.WebSocketException:
            logging.warning(
                "Attempted to probe CDU device %s at endpoint %s but device wasn't available",
                device,
                device_endpoint,
            )
            continue

    return available_devices


async def main():
    available_devices = await get_available_devices()

    tasks = []
    for device in available_devices:
        queue = asyncio.Queue()

        tasks.append(asyncio.create_task(handle_dataref_updates(queue, device)))
        tasks.append(asyncio.create_task(handle_device_update(queue, device)))

    logging.info("Started background tasks for %s", available_devices)

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
