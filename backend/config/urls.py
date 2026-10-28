from django.urls import path

from api import views

urlpatterns = [
    path("api/health/", views.health, name="health"),
    path("api/geocode/", views.geocode, name="geocode"),
    path("api/plan/", views.plan, name="plan"),
]
