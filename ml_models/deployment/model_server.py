# 2. Implement complete model serving: ml_models/deployment/model_server.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import joblib
import numpy as np
from typing import List, Dict
import logging

app = FastAPI(title="FinTrack ML Model Server")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ModelServer:
    def __init__(self):
        self.models = self.load_models()
        self.feature_engineer = FeatureEngineer()
    
    def load_models(self) -> Dict:
        """Load all trained models"""
        models = {}
        model_paths = [
            'stocks_xgb', 'stocks_lstm',
            'crypto_xgb', 'crypto_lstm',
            'etfs_xgb', 'etfs_lstm'
        ]
        
        for model_name in model_paths:
            try:
                models[model_name] = joblib.load(f"ml_models/saved_models/{model_name}.pkl")
                logging.info(f"Loaded model: {model_name}")
            except:
                logging.warning(f"Could not load model: {model_name}")
        
        return models

@app.post("/predict")
async def predict(features: Dict, model_type: str = "xgboost"):
    """Make prediction with selected model"""
    try:
        # Prepare features
        processed_features = feature_engineer.transform(features)
        
        # Select model
        model_key = f"{features['asset_type']}_{model_type}"
        model = model_server.models.get(model_key)
        
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")
        
        # Make prediction
        prediction = model.predict(processed_features)
        confidence = calculate_confidence(prediction, model)
        
        return {
            "prediction": float(prediction[0]),
            "confidence": confidence,
            "model": model_key,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "models_loaded": len(model_server.models)}

# Run with: uvicorn model_server:app --host 0.0.0.0 --port 8001