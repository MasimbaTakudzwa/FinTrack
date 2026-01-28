# scripts/train_test_models.py
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import yfinance as yf

# Generate synthetic test data if no real data available
def generate_test_data():
    """Generate synthetic market data for testing"""
    dates = pd.date_range(start='2023-01-01', end='2023-12-31', freq='D')
    np.random.seed(42)
    
    data = pd.DataFrame({
        'date': dates,
        'open': np.random.normal(100, 10, len(dates)).cumsum(),
        'high': np.random.normal(105, 10, len(dates)).cumsum(),
        'low': np.random.normal(95, 10, len(dates)).cumsum(),
        'close': np.random.normal(100, 10, len(dates)).cumsum(),
        'volume': np.random.randint(1000000, 10000000, len(dates))
    })
    
    # Add returns
    data['returns'] = data['close'].pct_change()
    data['next_day_return'] = data['returns'].shift(-1)
    
    return data.dropna()

def test_feature_engineering():
    """Test the feature engineering pipeline"""
    print("Testing Feature Engineering...")
    from data_pipeline.processing.feature_engineer import FeatureEngineer
    
    # Create test data
    test_df = generate_test_data()
    
    # Initialize and test feature engineer
    fe = FeatureEngineer()
    df_with_features = fe.add_technical_indicators(test_df)
    df_with_features = fe.add_time_features(df_with_features)
    df_with_features = fe.create_lag_features(df_with_features, ['close', 'volume'], [1, 2, 3])
    
    print(f"Original columns: {len(test_df.columns)}")
    print(f"After feature engineering: {len(df_with_features.columns)}")
    print(f"Sample features: {list(df_with_features.columns[-10:])}")
    
    return df_with_features

def test_sentiment_analysis():
    """Test sentiment analysis"""
    print("\nTesting Sentiment Analysis...")
    from backend.apps.news.sentiment_analysis import SentimentAnalyzer
    
    analyzer = SentimentAnalyzer()
    
    test_texts = [
        "Apple reports record quarterly profits exceeding all analyst expectations",
        "Market crashes as inflation fears trigger massive sell-off",
        "Federal Reserve announces interest rate hike of 0.25%",
        "Tesla stock remains stable despite broader market volatility"
    ]
    
    for text in test_texts:
        result = analyzer.analyze_news_sentiment(text)
        print(f"\nText: {text[:50]}...")
        print(f"Sentiment: {result['sentiment_label']} (Score: {result['aggregate_score']:.3f})")
    
    return analyzer

def test_training_pipeline():
    """Test the training pipeline with synthetic data"""
    print("\nTesting Training Pipeline...")
    from ml_models.training.train_pipeline import MLTrainingPipeline
    
    # Generate test data
    test_df = generate_test_data()
    
    # Initialize pipeline
    trainer = MLTrainingPipeline(experiment_name="test_training")
    
    # Prepare features
    X = test_df.drop(columns=['next_day_return', 'date'])
    y = test_df['next_day_return']
    
    # Train a simple model
    print("Training XGBoost model...")
    model = trainer.train_xgboost(X, y, asset_type='test')
    
    # Make predictions
    predictions = model.predict(X.head(10))
    print(f"Sample predictions: {predictions[:5]}")
    
    # Save model
    trainer.save_model('test_xgb', 'ml_models/saved_models/test_xgb.pkl')
    
    return model

if __name__ == "__main__":
    print("=" * 60)
    print("FINTRACK ML SYSTEM - INITIAL TESTING")
    print("=" * 60)
    
    # Test feature engineering
    df = test_feature_engineering()
    
    # Test sentiment analysis
    analyzer = test_sentiment_analysis()
    
    # Test training pipeline
    model = test_training_pipeline()
    
    print("\n" + "=" * 60)
    print("INITIAL TESTING COMPLETE!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Run this script: python scripts/train_test_models.py")
    print("2. Check saved models in ml_models/saved_models/")
    print("3. Test API endpoints with Postman/curl")
    print("4. Deploy model server")