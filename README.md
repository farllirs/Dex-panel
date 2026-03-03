# Dex Panel

Panel oficial de DEX con soporte de estilos:

- `Default`
- `Windows`
- `macOS Dock`

Version actual del proyecto: `1.0.0` (segun `metadata.json`).

## Caracteristicas

- Panel superior o inferior.
- Temas integrados + importacion de temas ZIP.
- Favoritos de aplicaciones.
- Seguimiento de ventanas activas con Wnck.
- Reloj y fecha configurables.
- Menu contextual de acciones rapidas.
- Dialogos de Preferencias, Temas y Favoritos.
- Modo macOS con magnificacion de iconos.

## Archivos principales

- App principal: `main.py`
- Metadata: `metadata.json`
- Config del proyecto: `config.json`
- Iconos del proyecto: `icons/`
- Fallback de iconos Google: `icons/Google/`

## Notas de iconos

- El icono principal empaquetado se define en `metadata.json`:
- `icon: "icons/app-icon.png"`
- Si quieres iconos por estilo (Windows/macOS), pueden convivir en `icons/` y cargarse desde `main.py` segun configuracion.

## Recomendacion para release

- Mantener todos los recursos dentro del proyecto (`./icons`) para empaquetado limpio (`.deb`).
- Evitar dependencias en rutas de usuario para recursos visuales.
