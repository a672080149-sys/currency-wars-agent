@echo off
rem Start cw-bridge elevated, once. No self-relaunch loop.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',('%~dp0run_server.ps1')"
