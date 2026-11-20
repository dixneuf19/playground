"""LiveKit access tokens: a JWT signed with the server's API secret."""

import time

import jwt

from coco_lanta.config import Settings

TTL = 12 * 3600  # one party


def access_token(cfg: Settings, identity: str, name: str, publish: bool) -> str:
    """Cameras publish and see nobody; screens watch and publish nothing."""
    now = int(time.time())
    return jwt.encode(
        {
            "iss": cfg.livekit_api_key,
            "sub": identity,
            "name": name,
            "nbf": now - 10,
            "exp": now + TTL,
            "video": {
                "room": cfg.livekit_room,
                "roomJoin": True,
                "canPublish": publish,
                "canSubscribe": not publish,
                "canPublishData": False,
            },
        },
        cfg.livekit_api_secret,
        algorithm="HS256",
    )
