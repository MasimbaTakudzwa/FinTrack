from transformers import pipeline
import nltk
from nltk.sentiment import SentimentIntensityAnalyzer
from typing import Dict, List, Tuple
import logging

class SentimentAnalyzer:
    def __init__(self, model_name: str = "ProsusAI/finbert"):
        """Initialize sentiment analysis models"""
        try:
            self.finbert = pipeline("sentiment-analysis", model=model_name)
        except:
            logging.warning(f"Could not load {model_name}, using fallback")
            self.finbert = None
        
        nltk.download('vader_lexicon', quiet=True)
        self.sia = SentimentIntensityAnalyzer()
        
    def analyze_news_sentiment(self, text: str) -> Dict:
        """Analyze sentiment of news article"""
        results = {}
        
        # VADER sentiment
        vader_scores = self.sia.polarity_scores(text)
        results['vader'] = {
            'compound': vader_scores['compound'],
            'positive': vader_scores['pos'],
            'negative': vader_scores['neg'],
            'neutral': vader_scores['neu']
        }
        
        # FinBERT if available
        if self.finbert:
            try:
                finbert_result = self.finbert(text[:512])[0]  # Limit text length
                results['finbert'] = {
                    'label': finbert_result['label'],
                    'score': finbert_result['score']
                }
            except:
                results['finbert'] = {'label': 'ERROR', 'score': 0.0}
        
        # Aggregate sentiment score
        results['aggregate_score'] = self._aggregate_sentiment(results)
        results['sentiment_label'] = self._get_sentiment_label(results['aggregate_score'])
        
        return results
    
    def _aggregate_sentiment(self, sentiment_results: Dict) -> float:
        """Aggregate multiple sentiment scores"""
        if 'finbert' in sentiment_results:
            finbert_score = sentiment_results['finbert']['score']
            if sentiment_results['finbert']['label'] == 'negative':
                finbert_score = -finbert_score
            elif sentiment_results['finbert']['label'] == 'neutral':
                finbert_score = 0
        else:
            finbert_score = 0
        
        vader_score = sentiment_results['vader']['compound']
        
        # Weighted average (adjust weights based on validation)
        aggregate = (0.6 * finbert_score + 0.4 * vader_score) if finbert_score != 0 else vader_score
        
        return aggregate
    
    def _get_sentiment_label(self, score: float) -> str:
        """Convert score to sentiment label"""
        if score > 0.3:
            return "very_positive"
        elif score > 0.1:
            return "positive"
        elif score > -0.1:
            return "neutral"
        elif score > -0.3:
            return "negative"
        else:
            return "very_negative"
    
    def batch_analyze(self, texts: List[str]) -> List[Dict]:
        """Analyze multiple texts efficiently"""
        return [self.analyze_news_sentiment(text) for text in texts]