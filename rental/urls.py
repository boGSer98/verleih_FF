from django.urls import path

from . import views

app_name = 'rental'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('vorgaenge/<int:pk>/uebergabe/', views.handover, name='handover'),
    path('vorgaenge/<int:pk>/ruecknahme/', views.return_case, name='return'),
    path('vorgaenge/<int:pk>/dokumente/reservierung/', views.generate_reservation_document, name='reservation_document'),
    path('vorgaenge/<int:pk>/dokumente/reservierung/senden/', views.send_reservation_document, name='reservation_document_send'),
    path('vorgaenge/<int:pk>/dokumente/uebergabe/', views.generate_handover_document, name='handover_document'),
    path('vorgaenge/<int:pk>/dokumente/uebergabe/senden/', views.send_handover_document, name='handover_document_send'),
    path('vorgaenge/<int:pk>/dokumente/ruecknahme/', views.generate_return_document, name='return_document'),
    path('vorgaenge/<int:pk>/dokumente/ruecknahme/senden/', views.send_return_document, name='return_document_send'),
    path('vorgaenge/<int:pk>/dokumente/abschluss/', views.generate_closing_document, name='closing_document'),
    path('vorgaenge/<int:pk>/dokumente/abschluss/senden/', views.send_closing_document, name='closing_document_send'),
    path('dokumente/<int:pk>/senden/', views.send_document, name='document_send'),
    path('dokumente/<int:pk>/', views.document_download, name='document_download'),
]
