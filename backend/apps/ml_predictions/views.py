from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.core.cache import cache
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any
import logging

from .prediction_models.price_predictor import PricePredictor
from .prediction_models.sentiment_model import SentimentModel

class PredictionAPIView(APIView):
    """API endpoints for ML predictions"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.price_predictor = PricePredictor()
        self.sentiment_model = SentimentModel()
    
    def get(self, request, format=None):
        """Get predictions for specified assets"""
        symbol = request.query_params.get('symbol', 'AAPL')
        horizon = request.query_params.get('horizon', '1d')  # 1d, 1w, 1m
        model_type = request.query_params.get('model', 'xgboost')
        
        # Check cache first
        cache_key = f"prediction_{symbol}_{horizon}_{model_type}"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            return Response(cached_result)
        
        try:
            # Get prediction
            prediction = self.price_predictor.predict(
                symbol=symbol,
                horizon=horizon,
                model_type=model_type
            )
            
            # Get confidence score
            confidence = self.price_predictor.get_confidence_score(prediction)
            
            # Get sentiment analysis if available
            sentiment = self.sentiment_model.get_asset_sentiment(symbol)
            
            result = {
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
                'prediction': prediction,
                'confidence': confidence,
                'sentiment': sentiment,
                'horizon': horizon,
                'model_used': model_type,
                'disclaimer': 'Predictions are for informational purposes only'
            }
            
            # Cache for 5 minutes
            cache.set(cache_key, result, timeout=300)
            
            return Response(result)
            
        except Exception as e:
            logging.error(f"Prediction error: {str(e)}")
            return Response(
                {'error': 'Prediction service temporarily unavailable'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
    
    def post(self, request, format=None):
        """Batch predictions for multiple assets"""
        symbols = request.data.get('symbols', [])
        horizon = request.data.get('horizon', '1d')
        
        results = []
        for symbol in symbols[:10]:  # Limit to 10 symbols per request
            try:
                prediction = self.price_predictor.predict(symbol, horizon)
                results.append({
                    'symbol': symbol,
                    'prediction': prediction,
                    'confidence': self.price_predictor.get_confidence_score(prediction)
                })
            except Exception as e:
                results.append({
                    'symbol': symbol,
                    'error': str(e)
                })
        
        return Response({
            'timestamp': datetime.now().isoformat(),
            'predictions': results
        })

class SentimentAnalysisAPIView(APIView):
    """API for sentiment analysis"""
    
    def post(self, request, format=None):
        """Analyze sentiment of provided text"""
        text = request.data.get('text', '')
        
        if not text:
            return Response(
                {'error': 'No text provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            sentiment = self.sentiment_model.analyze(text)
            return Response(sentiment)
        except Exception as e:
            logging.error(f"Sentiment analysis error: {str(e)}")
            return Response(
                {'error': 'Sentiment analysis failed'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )