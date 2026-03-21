from fastapi import FastAPI
from .api.routes import router
from .core.logger import logger

app = FastAPI(title="Sanjeevani WhatsApp Chatbot API")

# Include routes
app.include_router(router)

@app.get("/")
async def root():
    return {"status": "ok", "message": "Sanjeevani WhatsApp Chatbot is running"}

if __name__ == "__main__":
    import uvicorn
    # Use uvicorn to run the app during development
    logger.info("Starting Sanjeevani WhatsApp Chatbot locally...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
