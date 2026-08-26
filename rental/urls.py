from django.urls import path

from . import views

app_name = 'rental'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('vorgaenge/<int:pk>/uebergabe/', views.handover, name='handover'),
    path('vorgaenge/<int:pk>/ruecknahme/', views.return_case, name='return'),
]
