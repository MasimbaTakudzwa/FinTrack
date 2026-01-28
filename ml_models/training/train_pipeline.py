import pandas as pd
import numpy as np
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV
from sklearn.preprocessing import StandardScaler
import xgboost as xgb
import joblib
import mlflow
import mlflow.sklearn
from typing import Dict, Tuple, Any
import logging

class MLTrainingPipeline:
    def __init__(self, experiment_name: str = "fintrack_ml"):
        """Initialize ML training pipeline"""
        self.experiment_name = experiment_name
        self.models = {}
        self.scalers = {}
        
        # Setup MLflow
        mlflow.set_experiment(experiment_name)
    
    def prepare_features(self, df: pd.DataFrame, target_col: str = 'next_day_return') -> Tuple[pd.DataFrame, pd.Series]:
        """Prepare features and target for training"""
        # Separate features and target
        X = df.drop(columns=[target_col])
        y = df[target_col]
        
        # Handle missing values
        X = X.fillna(method='ffill').fillna(method='bfill')
        
        return X, y
    
    def train_xgboost(self, X_train: pd.DataFrame, y_train: pd.Series, 
                     asset_type: str = 'stock') -> xgb.XGBRegressor:
        """Train XGBoost model for price prediction"""
        
        with mlflow.start_run(run_name=f"xgboost_{asset_type}"):
            # Define model
            model = xgb.XGBRegressor(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                random_state=42,
                n_jobs=-1
            )
            
            # Time series cross-validation
            tscv = TimeSeriesSplit(n_splits=5)
            
            # Hyperparameter tuning
            param_grid = {
                'max_depth': [3, 5, 7],
                'learning_rate': [0.01, 0.1, 0.2],
                'n_estimators': [100, 200, 300],
                'subsample': [0.8, 0.9, 1.0]
            }
            
            grid_search = GridSearchCV(
                estimator=model,
                param_grid=param_grid,
                cv=tscv,
                scoring='neg_mean_squared_error',
                verbose=1,
                n_jobs=-1
            )
            
            # Train model
            grid_search.fit(X_train, y_train)
            best_model = grid_search.best_estimator_
            
            # Log parameters and metrics
            mlflow.log_params(grid_search.best_params_)
            mlflow.log_metric("best_score", grid_search.best_score_)
            
            # Log model
            mlflow.sklearn.log_model(best_model, "model")
            
            self.models[f'xgboost_{asset_type}'] = best_model
            
            return best_model
    
    def train_lstm(self, X_train: np.ndarray, y_train: np.ndarray, 
                  sequence_length: int = 30) -> Any:
        """Train LSTM model for sequence prediction"""
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout
        import tensorflow as tf
        
        with mlflow.start_run(run_name="lstm_price_prediction"):
            # Reshape data for LSTM
            n_samples = X_train.shape[0] - sequence_length + 1
            X_seq = np.array([X_train[i:i+sequence_length] for i in range(n_samples)])
            y_seq = y_train[sequence_length-1:]
            
            # Build LSTM model
            model = Sequential([
                LSTM(50, return_sequences=True, input_shape=(sequence_length, X_seq.shape[2])),
                Dropout(0.2),
                LSTM(50, return_sequences=False),
                Dropout(0.2),
                Dense(25),
                Dense(1)
            ])
            
            model.compile(optimizer='adam', loss='mse', metrics=['mae'])
            
            # Train model
            history = model.fit(
                X_seq, y_seq,
                epochs=50,
                batch_size=32,
                validation_split=0.2,
                verbose=1
            )
            
            # Log metrics
            for epoch, (loss, val_loss) in enumerate(zip(history.history['loss'], history.history['val_loss'])):
                mlflow.log_metric("train_loss", loss, step=epoch)
                mlflow.log_metric("val_loss", val_loss, step=epoch)
            
            self.models['lstm'] = model
            
            return model
    
    def save_model(self, model_name: str, path: str):
        """Save trained model to disk"""
        model = self.models.get(model_name)
        if model:
            joblib.dump(model, path)
            logging.info(f"Model {model_name} saved to {path}")
    
    def load_model(self, model_name: str, path: str):
        """Load model from disk"""
        model = joblib.load(path)
        self.models[model_name] = model
        return model