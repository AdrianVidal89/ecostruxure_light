from django.urls import path

from . import views

app_name = "ai"

urlpatterns = [
    path("", views.tools, name="tools"),
    path("connect/", views.connect, name="connect"),
    path("disconnect/", views.disconnect, name="disconnect"),
    path("chat/", views.chat, name="chat"),
    path("reset/", views.reset, name="reset"),
    path("theme/", views.toggle_theme, name="toggle_theme"),
]
