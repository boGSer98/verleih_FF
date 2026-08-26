from django.urls import path

from . import views

app_name = 'rental'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('vorgaenge/<int:pk>/uebergabe/', views.handover, name='handover'),
    path('vorgaenge/<int:pk>/ruecknahme/', views.return_case, name='return'),
    path('vorgaenge/<int:pk>/dokumente/reservierung/', views.generate_reservation_document, name='reservation_document'),
    path('dokumente/<int:pk>/', views.document_download, name='document_download'),
]
