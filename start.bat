@echo off
setlocal
cd /d "%~dp0"
site_document_unloader.exe --config config.yaml
