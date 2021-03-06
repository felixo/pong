import asyncio
import json
import logging

from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseSettings
from websockets import ConnectionClosedOK, ConnectionClosedError


class Settings(BaseSettings):
    ws_base_url: str = "localhost:8000"


settings = Settings()
app = FastAPI()
templates = Jinja2Templates(directory="templates")


app.mount("/static", StaticFiles(directory="static"), name="favicon.ico")
app.mount("/static", StaticFiles(directory="static"), name="pong")

# - base size of rackets (pads)
PAD_WIDTH = 80
PAD_HEIGHT = 10

# - default objects positions
PAD1_DEFAULT_POSITION = (399, 158)
PAD2_DEFAULT_POSITION = (9, 158)
PAD3_DEFAULT_POSITION = (158, 9)
PAD4_DEFAULT_POSITION = (158, 400)
BALL_DEFAULT_POSITION = (389, PAD1_DEFAULT_POSITION[1] + PAD_WIDTH/2 - 5)

# - base speeds
BALL_SPEED = 10
TOP_VER_SPEED = 15
LEFT_VER_SPEED = 15


# - cache for broadcasting to players
FIELD = {
    "pad1": {
        "left": PAD1_DEFAULT_POSITION[0], "top": PAD1_DEFAULT_POSITION[1],
        "active": False,
    },
    "pad2": {
        "left": PAD2_DEFAULT_POSITION[0], "top": PAD2_DEFAULT_POSITION[1],
        "active": False,
    },
    "pad3": {
        "left": PAD3_DEFAULT_POSITION[0], "top": PAD3_DEFAULT_POSITION[1],
        "active": False,
    },
    "pad4": {
        "left": PAD4_DEFAULT_POSITION[0], "top": PAD4_DEFAULT_POSITION[1],
        "active": False,
    },
    "ball": {
        "left": BALL_DEFAULT_POSITION[0], "top": BALL_DEFAULT_POSITION[1],
    },
}


# - cache for mapping client_id and pad's name
PLAYERS = {}

# - cache for system status which we will not send to players
ALL_SYS = {
    "pad1": {
        "name": "Client #{client_id} you are playing pad1 Right pad",
        "default_position": PAD1_DEFAULT_POSITION,
        "with_ball": True,
    },
    "pad2": {
        "name": "Client #{client_id} you are playing pad2 Left pad",
        "default_position": PAD2_DEFAULT_POSITION,
        "with_ball": False,
    },
    "pad3": {
        "name": "Client #{client_id} you are playing pad3 Top pad",
        "default_position": PAD3_DEFAULT_POSITION,
        "with_ball": False,
    },
    "pad4": {
        "name": "Client #{client_id} you are playing pad4 Bottom pad",
        "default_position": PAD4_DEFAULT_POSITION,
        "with_ball": False,
    },
    "ball": {
        "speed": {"top": 0, "left": 0},
    },
    "last_touch": "pad1",
    "score": {"pad1": 0, "pad2": 0, "pad3": 0, "pad4": 0},
}


stuff_lock = asyncio.Lock()


def get_pad(client_id: int) -> str:
    """
    Function for mapping client_id -> pad_name
    :param client_id:
    :return: pad_name
    """
    return PLAYERS[client_id]


def pad_remove(client_id: int):
    """
    Function for player's disconnects
    :param client_id:
    :return:
    """
    pad_name = get_pad(client_id)

    if ALL_SYS[pad_name]["with_ball"]:
        kick_ball(client_id, disconnect=True)

    FIELD[pad_name]["active"] = False
    FIELD[pad_name]["left"] = ALL_SYS[pad_name]["default_position"][0]
    FIELD[pad_name]["top"] = ALL_SYS[pad_name]["default_position"][1]
    del PLAYERS[client_id]


def crete_pad(client_id: int):
    """
    Function for allocating pad to player
    :param client_id:
    :return:
    """
    PLAYERS[client_id] = None
    for k, v in FIELD.items():
        if not v["active"]:
            PLAYERS[client_id] = k
            v["active"] = True
            break


def kick_ball(client_id: int, disconnect: bool = False):
    """
    Function for start ball flies
    :param client_id:
    :param disconnect: This var for disconnecting player to prevent endless ball fly between 2 empty walls
    :return:
    """
    pad_name = get_pad(client_id)
    ALL_SYS[pad_name]["with_ball"] = False

    # - Base speed depend on pad_name
    if pad_name == "pad1":
        ALL_SYS["ball"]["speed"]["top"] = 0
        ALL_SYS["ball"]["speed"]["left"] = int(-BALL_SPEED / len(manager.active_connections))
    elif pad_name == "pad2":
        ALL_SYS["ball"]["speed"]["top"] = 0
        ALL_SYS["ball"]["speed"]["left"] = int(BALL_SPEED / len(manager.active_connections))
    elif pad_name == "pad3":
        ALL_SYS["ball"]["speed"]["top"] = int(BALL_SPEED / len(manager.active_connections))
        ALL_SYS["ball"]["speed"]["left"] = 0
    elif pad_name == "pad4":
        ALL_SYS["ball"]["speed"]["top"] = int(-BALL_SPEED / len(manager.active_connections))
        ALL_SYS["ball"]["speed"]["left"] = 0

    if disconnect:
        ALL_SYS["ball"]["speed"]["top"] += 1
        ALL_SYS["ball"]["speed"]["left"] += 1


async def move_ball() -> bool:
    """
    One of main function. Updates FIELD and ALL_SYS, so we can render it to players
    It's return True when someone got a score and we need to broadcast about it
    :return:
    """
    async with stuff_lock:
        FIELD["ball"]["top"] += ALL_SYS["ball"]["speed"]["top"]
        FIELD["ball"]["left"] += ALL_SYS["ball"]["speed"]["left"]

        bt = FIELD["ball"]["top"]
        bl = FIELD["ball"]["left"]

        # - now check collisions
        # TODO There we could update a lot:
        # todo better calc angle between 2 players

        # - check left border
        if bl <= 10:
            if FIELD["pad2"]["active"]:
                # - check collision with pad
                if check_pad_col("pad2"):
                    # - change direction
                    ALL_SYS["ball"]["speed"]["left"] = ALL_SYS["ball"]["speed"]["left"] * -1
                    ALL_SYS["last_touch"] = "pad2"
                    get_speed("pad2")
                else:
                    score("pad2")
                    return True
            else:
                ALL_SYS["ball"]["speed"]["left"] = ALL_SYS["ball"]["speed"]["left"] * -1

        # - check right border
        if bl >= 390:
            if FIELD["pad1"]["active"]:
                # - check collision
                if check_pad_col("pad1"):
                    # - change direction
                    ALL_SYS["ball"]["speed"]["left"] = ALL_SYS["ball"]["speed"]["left"] * -1
                    ALL_SYS["last_touch"] = "pad1"
                    get_speed("pad1")
                else:
                    score("pad1")
                    return True
            else:
                # - change direction
                ALL_SYS["ball"]["speed"]["left"] = ALL_SYS["ball"]["speed"]["left"] * -1

        # - check top border
        if bt <= 10:
            if FIELD["pad3"]["active"]:
                # - check collision
                if check_pad_col("pad3"):
                    # - change direction
                    ALL_SYS["ball"]["speed"]["top"] = ALL_SYS["ball"]["speed"]["top"] * -1
                    ALL_SYS["last_touch"] = "pad3"
                    get_speed("pad3")
                else:
                    score("pad3")
                    return True
            else:
                # - change direction
                ALL_SYS["ball"]["speed"]["top"] = ALL_SYS["ball"]["speed"]["top"] * -1

        # - check bottom border
        if bt >= 390:
            if FIELD["pad4"]["active"]:
                # - check collision
                if check_pad_col("pad4"):
                    # - change direction
                    ALL_SYS["ball"]["speed"]["top"] = ALL_SYS["ball"]["speed"]["top"] * -1
                    ALL_SYS["last_touch"] = "pad4"
                    get_speed("pad4")
                else:
                    score("pad4")
                    return True
            else:
                # - change direction
                ALL_SYS["ball"]["speed"]["top"] = ALL_SYS["ball"]["speed"]["top"] * -1

        return False


def check_pad_col(pad_name: str) -> bool:
    """
    This function we call when we have ball collision with one of border
    And we need to know do we have collision in this position with pads
    :param pad_name:
    :return:
    """
    bpt = FIELD["ball"]["top"]
    bpl = FIELD["ball"]["left"]

    if pad_name in ("pad1", "pad2"):
        pptt = FIELD[pad_name]["top"]
        pptb = pptt + PAD_WIDTH + 10

        if pptt <= bpt <= pptb:
            return True
    else:
        ppll = FIELD[pad_name]["left"]
        pplr = FIELD[pad_name]["left"] + PAD_WIDTH + 10
        if ppll <= bpl <= pplr:
            return True

    return False


def get_speed(pad_name: str = None):
    """
    This function change ball speed after collision
    :param pad_name:
    :return:
    """
    if pad_name is None:
        # - calculate empty side - save this place for updates
        # TODO calculate more complex collision with mirror wall
        pass
    else:
        # - calculate speed after collision with pad
        # - TODO make more complex: use math formula with "Curve radius"
        if pad_name in ("pad1", "pad2"):
            bpt = FIELD["ball"]["top"]
            pptt = FIELD[pad_name]["top"] + PAD_WIDTH / 2
            diff = abs(pptt - bpt)
            if diff < PAD_WIDTH / 5:
                ALL_SYS["ball"]["speed"]["top"] = int(4 / len(manager.active_connections) + 1)
            elif PAD_WIDTH / 5 < diff < PAD_WIDTH / 3:
                ALL_SYS["ball"]["speed"]["top"] = int(8 / len(manager.active_connections) + 1)
            elif diff > PAD_WIDTH / 3:
                ALL_SYS["ball"]["speed"]["top"] = int(10 / len(manager.active_connections) + 1)
            if pptt - bpt >= 0:
                ALL_SYS["ball"]["speed"]["top"] = ALL_SYS["ball"]["speed"]["top"] * -1
        else:
            bpl = FIELD["ball"]["left"]
            pplr = FIELD[pad_name]["left"] + PAD_WIDTH / 2
            diff = abs(pplr - bpl)
            if diff < PAD_WIDTH / 5:
                ALL_SYS["ball"]["speed"]["left"] = int(4 / len(manager.active_connections))
            elif PAD_WIDTH / 5 < diff < PAD_WIDTH / 3:
                ALL_SYS["ball"]["speed"]["left"] = int(8 / len(manager.active_connections))
            elif diff > PAD_WIDTH / 3:
                ALL_SYS["ball"]["speed"]["left"] = int(10 / len(manager.active_connections))
            if pplr - bpl >= 0:
                ALL_SYS["ball"]["speed"]["left"] = ALL_SYS["ball"]["speed"]["left"] * -1


def reset_score():
    """
    Function for scores reset
    :return:
    """
    for k in ALL_SYS["score"].keys():
        ALL_SYS["score"][k] = 0


def score(pad_name: str):
    """
    This function calculate scores and reset ball
    :param pad_name:
    :return:
    """
    ALL_SYS[pad_name]["with_ball"] = True
    ALL_SYS["ball"]["speed"]["top"] = 0
    ALL_SYS["ball"]["speed"]["left"] = 0

    shift_top = PAD_WIDTH / 2 - 5 if pad_name in ("pad1", "pad2") else PAD_HEIGHT
    shit_left = PAD_HEIGHT if pad_name in ("pad1", "pad2") else PAD_WIDTH / 2 - 5

    if pad_name == "pad1":
        shit_left = shit_left * -1
    if pad_name == "pad4":
        shift_top = shift_top * -1 - 1

    FIELD["ball"]["top"] = FIELD[pad_name]["top"] + shift_top
    FIELD["ball"]["left"] = FIELD[pad_name]["left"] + shit_left

    ALL_SYS["score"][ALL_SYS["last_touch"]] += 1
    ALL_SYS["last_touch"] = pad_name


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket, client_id: int):
        if client_id in PLAYERS:
            pad_remove(client_id)
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

    async def move_pad(self, client_id: int, key: str):
        """
        This method used to move racket (pad)
        If ball connected to pad we move it together.
        :param client_id:
        :param key:
        :return:
        """
        # TODO later we need check accuracy of movement with ball and replace all integer on const
        pad_name = get_pad(client_id)
        if pad_name in ("pad1", "pad2"):
            if key == "ArrowUp":
                # - move pad
                FIELD[pad_name]["top"] -= TOP_VER_SPEED
                if FIELD[pad_name]["top"] <= 8:
                    FIELD[pad_name]["top"] = 8

                # - move ball
                if ALL_SYS[pad_name]["with_ball"]:
                    FIELD["ball"]["top"] -= TOP_VER_SPEED
                    if FIELD["ball"]["top"] <= PAD_WIDTH/2 + 5:
                        FIELD["ball"]["top"] = PAD_WIDTH/2 + 5

            if key == "ArrowDown":
                # - move pad
                FIELD[pad_name]["top"] += TOP_VER_SPEED
                if FIELD[pad_name]["top"] >= 400 - PAD_WIDTH + 10:
                    FIELD[pad_name]["top"] = 400 - PAD_WIDTH + 10

                # - move ball
                if ALL_SYS[pad_name]["with_ball"]:
                    FIELD["ball"]["top"] += TOP_VER_SPEED
                    if FIELD["ball"]["top"] >= 400 - PAD_WIDTH / 2 + 5:
                        FIELD["ball"]["top"] = 400 - PAD_WIDTH / 2 + 5

        if pad_name in ("pad3", "pad4"):
            if key == "ArrowLeft":
                # - move pad
                FIELD[pad_name]["left"] -= LEFT_VER_SPEED
                if FIELD[pad_name]["left"] <= 8:
                    FIELD[pad_name]["left"] = 8

                # - move ball
                if ALL_SYS[pad_name]["with_ball"]:
                    FIELD["ball"]["left"] -= LEFT_VER_SPEED
                    if FIELD["ball"]["left"] <= PAD_WIDTH/2 + 5:
                        FIELD["ball"]["left"] = int(PAD_WIDTH/2 + 5)

            if key == "ArrowRight":
                # - move pad
                FIELD[pad_name]["left"] += LEFT_VER_SPEED
                if FIELD[pad_name]["left"] >= 400 - PAD_WIDTH + 10:
                    FIELD[pad_name]["left"] = 400 - PAD_WIDTH + 10

                # - move ball
                if ALL_SYS[pad_name]["with_ball"]:
                    FIELD["ball"]["left"] += LEFT_VER_SPEED
                    if FIELD["ball"]["left"] >= 400 - PAD_WIDTH/2 + 5:
                        FIELD["ball"]["left"] = int(400 - PAD_WIDTH/2 + 5)


manager = ConnectionManager()


@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    return templates.TemplateResponse("pong.html", {"request": request, "r_base_url": settings.ws_base_url})


@app.websocket("/ws/pong/{client_id}")
async def websocket_pong_endpoint(websocket: WebSocket, client_id: int):
    await manager.connect(websocket)
    await manager.broadcast(json.dumps({"info": f"Client #{client_id} entered game"}))

    if len(PLAYERS) >= 4:
        await manager.send_personal_message(json.dumps({"info": "All Pads are in use. You can only watch"}), websocket)
    else:
        crete_pad(client_id)
        welcome_msg = ALL_SYS[get_pad(client_id)]["name"].format(client_id=client_id)
        await manager.send_personal_message(json.dumps({"info": welcome_msg}), websocket)

    try:
        while True:
            data = await websocket.receive_text()
            if data in ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"):
                await manager.move_pad(client_id, data)
            if data == "Enter" and ALL_SYS[get_pad(client_id)]["with_ball"]:
                kick_ball(client_id)
            if data == "Reset":
                reset_score()
                await manager.broadcast(json.dumps({"info": "Score: {}".format(json.dumps(ALL_SYS["score"]))}))
    except WebSocketDisconnect:
        manager.disconnect(websocket, client_id)
        await manager.broadcast(json.dumps({"info": f"Client #{client_id} has left game"}))


@app.websocket("/ws/field/")
async def websocket_field_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        scored = await move_ball()

        if scored:
            await manager.broadcast(json.dumps({"info": "Score: {}".format(json.dumps(ALL_SYS["score"]))}))
        try:
            await websocket.send_text(json.dumps(FIELD))
        except (WebSocketDisconnect, ConnectionClosedOK, ConnectionClosedError):
            await websocket.close()
            break
        except Exception as exc:
            logging.info(exc)
            break
        await asyncio.sleep(0.033)


path = "favicon.ico"


@app.get('/favicon.ico')
async def favicon():
    return FileResponse(path)
