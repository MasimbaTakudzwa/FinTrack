# 4. Implement model monitoring: ml_models/deployment/model_monitor.py
import schedule
import time
import json
import logging
from datetime import datetime
from typing import Dict, Any
import pandas as pd
from prometheus_client import start_http_server, Gauge, Counter

class ModelMonitor:
    def __init__(self):
        self.metrics = {
            'prediction_latency': Gauge('prediction_latency_seconds', 'Prediction latency in seconds'),
            'model_accuracy': Gauge('model_accuracy_percent', 'Model accuracy percentage'),
            'predictions_total': Counter('predictions_total', 'Total number of predictions'),
            'errors_total': Counter('prediction_errors_total', 'Total prediction errors')
        }
        
        self.alert_thresholds = {
            'accuracy_drop': 0.1,  # 10% drop
            'latency_increase': 2.0,  # 2x increase
            'error_rate': 0.05  # 5% error rate
        }
    
    def start_monitoring(self):
        """Start monitoring server and schedule checks"""
        # Start Prometheus metrics server
        start_http_server(8002)
        
        # Schedule monitoring tasks
        schedule.every(5).minutes.do(self.check_model_health)
        schedule.every().hour.do(self.check_data_drift)
        schedule.every().day.at("02:00").do(self.generate_daily_report)
        
        logging.info("Model monitoring started")
        
        while True:
            schedule.run_pending()
            time.sleep(1)
    
    def check_model_health(self):
        """Check health of all deployed models"""
        for model_name, model in self.loaded_models.items():
            try:
                # Test prediction with sample data
                start_time = time.time()
                prediction = model.predict(self.sample_data)
                latency = time.time() - start_time
                
                # Update metrics
                self.metrics['prediction_latency'].set(latency)
                self.metrics['predictions_total'].inc()
                
                # Check for anomalies
                if latency > self.alert_thresholds['latency_increase']:
                    self.send_alert(f"High latency detected for {model_name}: {latency}s")
                
            except Exception as e:
                self.metrics['errors_total'].inc()
                logging.error(f"Model {model_name} health check failed: {str(e)}")
    
    def check_data_drift(self):
        """Check for data drift in input features"""
        current_features = self.get_current_feature_distribution()
        reference_features = self.load_reference_distribution()
        
        # Calculate drift using Wasserstein distance or KL divergence
        drift_score = self.calculate_drift(current_features, reference_features)
        
        if drift_score > self.alert_thresholds['data_drift']:
            self.send_alert(f"Data drift detected: {drift_score}")
            
            # Trigger model retraining if drift is significant
            if drift_score > 0.3:
                self.trigger_model_retraining()
    
    def send_alert(self, message: str, level: str = "warning"):
        """Send alert through configured channels"""
        alert = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message,
            "component": "ml_models"
        }
        
        # Send to logging system
        logging.warning(f"ALERT: {message}")
        
        # Send to notification service (Slack, Email, etc.)
        self.send_notification(alert)
    
    def generate_daily_report(self):
        """Generate daily monitoring report"""
        report = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "models_monitored": len(self.loaded_models),
            "total_predictions": self.metrics['predictions_total']._value.get(),
            "error_rate": self.calculate_error_rate(),
            "average_latency": self.calculate_average_latency(),
            "alerts_generated": self.alert_count,
            "recommendations": self.generate_recommendations()
        }
        
        # Save report
        with open(f"reports/model_report_{datetime.now().strftime('%Y%m%d')}.json", "w") as f:
            json.dump(report, f, indent=2)
        
        return report