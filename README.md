# Radar Docente v3 — CABA + Avellaneda

La aplicación móvil es el frontend PWA. El backend hace las consultas. Para que el botón «Actualizar ahora» y la actualización diaria sean reales, el backend tiene que estar desplegado en un servidor encendido.

## Fuentes
- CABA: https://actopublico.bue.edu.ar/
- PBA/ABC APD: https://misservicios.abc.gob.ar/actos.publicos.digitales/

## ABC / sesión
La app no guarda tu contraseña. La sesión persistente debe iniciarse en el perfil Playwright del servidor. En un entorno de servidor se puede ejecutar una sesión no-headless una sola vez para iniciar sesión y después reutilizar el perfil.

## Ejecutar
docker compose up --build

API: http://localhost:8000/api/salud
Frontend: servir `frontend/` por HTTPS y cambiar `radar_api` al dominio de la API.

## Importante
La extracción de cada portal puede requerir adaptar selectores si cambian la interfaz. El monitor está preparado como base funcional y no postula por el usuario.
