from django.urls import path

from .views import OptimizeFuelRouteView


urlpatterns = [
    path('optimize/', OptimizeFuelRouteView.as_view(), name='optimize-fuel-route'),
]
