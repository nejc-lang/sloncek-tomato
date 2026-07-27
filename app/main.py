import hmac

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app.process import run

app = FastAPI(title="DN-PREVZEMNICA-Tomato")


def require_api_key(x_api_key: str = Header(default="")) -> None:
    """Preveri skupni skrivni kljuc, ki ga GUI posreduje v headerju `X-API-Key`.

    Ce backend nima nastavljenega API_KEY (prazen .env), so vsi klici
    zavrnjeni z 500 - to prepreci nehoteni zagon brez zascite v LAN.
    `hmac.compare_digest` se uporabi namesto `==`, da primerjava ni ranljiva
    na timing napade.
    """
    if not settings.API_KEY:
        raise HTTPException(status_code=500, detail="Backend nima nastavljenega API_KEY (.env)")
    if not hmac.compare_digest(x_api_key, settings.API_KEY):
        raise HTTPException(status_code=401, detail="Neveljaven ali manjkajoc X-API-Key")


@app.get("/")
def root():
    return {"status": "ok", "service": "DN-PREVZEMNICA-Tomato"}


@app.post("/api/obdelaj", dependencies=[Depends(require_api_key)])
def obdelaj():
    try:
        return JSONResponse(content=run())
    except Exception as exc:  # noqa: BLE001 - surface any error to the GUI log
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})
