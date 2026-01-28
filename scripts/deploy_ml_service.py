# scripts/deploy_ml_service.py
import subprocess
import time
import requests
import os
import sys

def check_dependencies():
    """Check if all required packages are installed"""
    required = [
        'fastapi',
        'uvicorn',
        'joblib',
        'numpy',
        'pandas',
        'scikit-learn',
        'xgboost'
    ]
    
    missing = []
    for package in required:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"Missing packages: {missing}")
        print("Install with: pip install " + " ".join(missing))
        return False
    
    return True

def start_model_server():
    """Start the FastAPI model server"""
    print("Starting Model Server...")
    
    # Check if port 8001 is available
    try:
        response = requests.get("http://localhost:8001/health", timeout=2)
        print(f"Model server already running: {response.status_code}")
        return True
    except:
        pass
    
    # Start server in background
    server_process = subprocess.Popen([
        sys.executable, "-m", "uvicorn",
        "ml_models.deployment.model_server:app",
        "--host", "0.0.0.0",
        "--port", "8001",
        "--reload"
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Wait for server to start
    for i in range(30):  # Wait up to 30 seconds
        time.sleep(1)
        try:
            response = requests.get("http://localhost:8001/health", timeout=2)
            if response.status_code == 200:
                print(f"✓ Model server started successfully")
                return server_process
        except:
            if i % 5 == 0:
                print(f"  Waiting for server to start... ({i+1}s)")
    
    print("✗ Failed to start model server")
    return None

def test_api_endpoints():
    """Test all API endpoints"""
    base_url = "http://localhost:8001"
    
    tests = [
        ("GET", "/health", None, 200),
        ("POST", "/predict", {
            "features": {
                "symbol": "AAPL",
                "close": 180.5,
                "volume": 75000000,
                "sma_7": 178.2,
                "sma_30": 175.8,
                "rsi": 65.5,
                "volatility": 0.015
            },
            "model_type": "xgb"
        }, 200),
    ]
    
    print("\nTesting API Endpoints...")
    for method, endpoint, data, expected_status in tests:
        try:
            if method == "GET":
                response = requests.get(f"{base_url}{endpoint}")
            elif method == "POST":
                response = requests.post(f"{base_url}{endpoint}", json=data)
            
            if response.status_code == expected_status:
                print(f"✓ {method} {endpoint}: {response.status_code}")
                if endpoint == "/predict" and response.status_code == 200:
                    result = response.json()
                    print(f"  Prediction: {result.get('prediction')}")
                    print(f"  Confidence: {result.get('confidence')}")
            else:
                print(f"✗ {method} {endpoint}: {response.status_code} (expected {expected_status})")
                
        except Exception as e:
            print(f"✗ {method} {endpoint}: Error - {str(e)}")

def integrate_with_backend():
    """Test integration with Django backend"""
    print("\nTesting Django Integration...")
    
    # Create a test Django view
    test_code = '''
# backend/apps/ml_predictions/views.py - Add this test endpoint
from django.http import JsonResponse
import requests

def test_ml_integration(request):
    """Test endpoint to verify ML service integration"""
    try:
        # Call ML service
        ml_response = requests.post(
            "http://localhost:8001/predict",
            json={
                "features": {
                    "symbol": "AAPL",
                    "close": 180.5,
                    "volume": 75000000
                }
            },
            timeout=5
        )
        
        if ml_response.status_code == 200:
            return JsonResponse({
                "status": "success",
                "ml_service": "connected",
                "prediction": ml_response.json()
            })
        else:
            return JsonResponse({
                "status": "error",
                "ml_service": "error",
                "detail": ml_response.text
            }, status=500)
            
    except Exception as e:
        return JsonResponse({
            "status": "error",
            "ml_service": "unavailable",
            "detail": str(e)
        }, status=503)
    '''
    
    print("Add this test view to verify integration:")
    print(test_code)
    
    # Create a test URL
    url_config = '''
# backend/urls.py - Add this route
from django.contrib import admin
from django.urls import path, include
from apps.ml_predictions.views import test_ml_integration

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/ml/test/', test_ml_integration, name='ml-test'),
    # ... other routes
]
    '''
    
    print("\nURL configuration:")
    print(url_config)

if __name__ == "__main__":
    print("=" * 60)
    print("FINTRACK ML SERVICE DEPLOYMENT")
    print("=" * 60)
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    # Start model server
    server = start_model_server()
    
    if server:
        # Test APIs
        test_api_endpoints()
        
        # Test Django integration
        integrate_with_backend()
        
        print("\n" + "=" * 60)
        print("DEPLOYMENT COMPLETE!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Access model server: http://localhost:8001")
        print("2. Test with: curl http://localhost:8001/health")
        print("3. Add Django integration as shown above")
        print("4. Create frontend components")
        
        # Keep server running
        try:
            print("\nModel server running. Press Ctrl+C to stop.")
            server.wait()
        except KeyboardInterrupt:
            print("\nStopping model server...")
            server.terminate()
    else:
        print("Failed to deploy ML service")