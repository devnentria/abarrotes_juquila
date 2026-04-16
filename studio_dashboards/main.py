"""
Módulo II — Studio de Dashboards
Punto de entrada de la aplicación FastAPI.

Estado: En espera — se construye después del PWA Asistente.

Cuando llegue el momento, aquí se montarán:
  - /studio             → UI del generador de dashboards
  - /api/studio/generar → IA genera Chart.js a partir de lenguaje natural
  - /api/dashboards     → CRUD de dashboards guardados
"""
import uvicorn
from fastapi import FastAPI

from shared.config import STUDIO_PORT

app = FastAPI(
    title="Studio Dashboards — Suite Analítica Nentria",
    docs_url=None,
    redoc_url=None,
)


@app.get("/health")
async def health():
    return {"status": "ok", "modulo": "studio_dashboards"}


if __name__ == "__main__":
    uvicorn.run(
        "studio_dashboards.main:app",
        host="0.0.0.0",
        port=STUDIO_PORT,
        reload=True,
    )
