# 1. Create and run this training script: ml_models/train_all.py
import pandas as pd
import numpy as np
from training.train_pipeline import MLTrainingPipeline
from data.dataset_builder import DatasetBuilder
from evaluation.backtester import Backtester

def train_and_validate():
    """Train all models and validate performance"""
    
    # 1. Build training datasets
    dataset_builder = DatasetBuilder()
    train_data, test_data = dataset_builder.load_and_split()
    
    # 2. Initialize training pipeline
    trainer = MLTrainingPipeline()
    
    # 3. Train multiple models
    models = {}
    for asset_type in ['stocks', 'crypto', 'etfs']:
        print(f"Training {asset_type} models...")
        
        # Train XGBoost
        X_train, y_train = trainer.prepare_features(train_data[asset_type])
        xgb_model = trainer.train_xgboost(X_train, y_train, asset_type)
        models[f'{asset_type}_xgb'] = xgb_model
        
        # Train LSTM
        X_seq = dataset_builder.create_sequences(train_data[asset_type])
        lstm_model = trainer.train_lstm(X_seq, y_train.values)
        models[f'{asset_type}_lstm'] = lstm_model
    
    # 4. Backtest all models
    backtester = Backtester()
    results = backtester.evaluate_all(models, test_data)
    
    # 5. Save best models
    for model_name, model in models.items():
        trainer.save_model(model_name, f"ml_models/saved_models/{model_name}.pkl")
    
    return results