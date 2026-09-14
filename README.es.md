[English](README.md) | [Русский](README.ru.md) | **[Español]**

# ISKER — Agente de IA para tareas rutinarias de desarrollo

Un agente de IA agnóstico al lenguaje de programación que se encarga de
la parte rutinaria del código: escribir funciones a partir de una
descripción, buscar y renombrar en todo el proyecto, refactorizar,
corregir errores a partir de una descripción o un log, y generar tests
y documentación.

Funciona **de la misma manera** con cualquier archivo de texto — GDScript
(Godot), Python, HTML/CSS/JS, etc. Las particularidades del lenguaje se
definen a nivel del prompt, no en el código de las herramientas, así que
pasar a otra pila tecnológica no requiere modificar el agente en sí.

Funciona con **niveles gratuitos** a través de una cadena de tres
proveedores (Groq → OpenRouter → Gemini), con cambio automático y
reintentos en caso de sobrecarga.

> Este es un proyecto personal para un portafolio, pero también una
> herramienta real que el autor usa en sus propios proyectos. **No
> reemplaza** la comprensión del código por parte del desarrollador —
> todo lo que requiere una decisión creativa (arquitectura, trabajo
> visual, elección de assets) lo sigue haciendo la persona.

---

## Captura de pantalla

![ISKER screenshot](docs/screenshot.png)

---

## Funcionalidades

- **Interfaz de escritorio** hecha con PySide6 — ventana de chat,
  historial de conversación, indicador de progreso por pasos (`[3/15]`),
  botón para cancelar la tarea.
- **`.exe` listo para Windows** — se puede descargar y ejecutar sin
  instalar Python (ver "Inicio rápido" más abajo).
- **Tres idiomas de interfaz**: ruso, inglés, español — se cambian al
  vuelo, sin reiniciar. El agente responde en el idioma en que se le
  hizo la pregunta, sin importar el idioma de la interfaz.
- **Resumen de la tarea** después de cada ejecución — qué se hizo
  exactamente y por qué, mostrado en un bloque aparte dentro del chat.
- **Cadena de respaldo entre proveedores con reintentos**: si un
  proveedor no está disponible, el agente cambia automáticamente al
  siguiente; si los tres fallan a la vez, espera y reintenta (5 y luego
  15 segundos) antes de informar un error.
- **Caché** de solicitudes repetidas dentro de una misma sesión.
- **Botón de cancelar**: una tarea larga se puede interrumpir entre
  pasos — sin deshacer los cambios ya aplicados, con un informe honesto
  de lo que alcanzó a completarse.
- **Modo CLI** (`main.py`) — para probar el núcleo desde una terminal,
  sin interfaz gráfica.

---

## Inicio rápido (sin instalar Python)

1. Ve a la sección [Releases](../../releases) de este repositorio.
2. Descarga el archivo `ISKER-windows.zip` de la última versión.
3. Extrae el **archivo completo** en cualquier carpeta.
4. Consigue al menos una clave API gratuita y completa el archivo
   `.env` dentro de la carpeta extraída — ver "Obtener claves API" más
   abajo, paso a paso.
5. Ejecuta `ISKER.exe`.

> **Importante:** el `.exe` no funciona por sí solo — la carpeta
> `_internal` (con todas las bibliotecas necesarias) debe estar justo
> al lado. Si quieres mover la aplicación a otro lugar (por ejemplo, al
> escritorio): copia **toda la carpeta** (`ISKER.exe` + `_internal`), o
> deja la carpeta donde está y crea un **acceso directo** a `ISKER.exe`
> (clic derecho → "Enviar a" → "Escritorio, crear acceso directo") —
> así es más fácil para futuras actualizaciones.
>
> En el primer inicio, Windows SmartScreen puede mostrar una advertencia
> ("Windows protegió tu PC") — es la reacción estándar ante un `.exe` de
> un desarrollador independiente sin firma digital de pago, no una señal
> de virus. Haz clic en "Más información" → "Ejecutar de todas formas".

---

## Obtener claves API y configurar `.env`

Necesitas al menos **una** clave de uno de los tres proveedores de
abajo — los tres son completamente gratuitos, el registro se hace desde
un navegador normal, sin tarjeta de crédito. Tener las tres a la vez
simplemente le permite al agente cambiar automáticamente entre ellas si
una está sobrecargada — no es obligatorio para empezar.

- **Groq**: https://console.groq.com/keys
- **OpenRouter**: https://openrouter.ai/keys
- **Gemini**: https://aistudio.google.com/apikey

Entra al sitio del proveedor que elijas, regístrate, busca un botón como
"Create API key" / "Get API key" — copia la cadena resultante
(normalmente empieza con algo como `gsk_...` o `AIza...`).

### Cómo ponerla en `.env` (sin experiencia en programación)

1. En la carpeta extraída, busca el archivo `.env.example`.
2. Cópialo y renombra la copia a `.env` (importante: sin `.example` al
   final — el punto antes de "env" debe quedarse).
3. Abre `.env` con cualquier editor de texto — sirve el Bloc de notas
   normal (clic derecho sobre el archivo → "Abrir con" → "Bloc de
   notas").
4. Busca la línea de tu proveedor y escribe la clave justo después del
   signo `=`, por ejemplo:
   ```
   GROQ_API_KEY=tu_clave_real_aqui
   ```
5. Guarda el archivo (`Ctrl+S`) y cierra el Bloc de notas.

Las líneas de los proveedores cuya clave no obtuviste se pueden dejar
vacías o comentadas (`#` al inicio de la línea) — el agente
simplemente no intentará usar ese proveedor.

### ⚠️ Los nombres de los modelos en `.env.example` pueden quedar desactualizados

Los proveedores retiran versiones antiguas de sus modelos de vez en
cuando. Si ves `LLM FAIL ... reason=HTTP 404` en los logs para algún
proveedor, significa que el modelo indicado en `.env` ya no existe. El
agente pasará automáticamente al siguiente proveedor de la cadena, pero
para un funcionamiento completo conviene actualizar `.env` con un
nombre de modelo vigente. Listas de modelos actualizadas:

- Groq: https://console.groq.com/docs/models
- OpenRouter (filtro `:free`): https://openrouter.ai/models
- Gemini: https://ai.google.dev/gemini-api/docs/models

---

## Instalación desde el código fuente (para desarrolladores)

```bash
git clone <url-de-este-repositorio>
cd isker
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Completa `.env` con tu clave — ver "Obtener claves API y configurar
`.env`" más arriba.

### Ejecutar la versión GUI

```bash
python -m ui.app
```

### Ejecutar la versión CLI (para pruebas en terminal)

```bash
python main.py
```

### Compilar tu propio `.exe`

```bash
pyinstaller --name ISKER --windowed --add-data "assets;assets" --hidden-import ui.app --icon assets/icon.ico run_gui.py
```

El `.exe` resultante aparecerá en `dist/ISKER/` junto con la carpeta
`_internal` — ambas partes deben moverse juntas (ver advertencia
arriba).

---

## Antes de trabajar con el agente

**Haz siempre un `git commit`** (o guarda una copia manual del
proyecto) antes de permitir que el agente escriba archivos. La
aplicación te lo recordará al iniciar una sesión con permiso de
escritura, pero la responsabilidad de guardar el trabajo es de la
persona — el agente no ejecuta comandos de git por sí mismo.

Al iniciar una sesión:
1. Indica la carpeta raíz del proyecto.
2. Permite la escritura en archivos (o déjalo solo en modo lectura —
   la opción por defecto).
3. Plantea las tareas en lenguaje libre, por ejemplo: "encuentra todos
   los lugares donde se usa health y muéstramelos" o "corrige el error:
   NullReferenceException en PlayerController.gd en la línea 42".

Atomicidad: si el agente se interrumpe a mitad de una edición de varios
pasos (error, cancelación manual), los cambios ya aplicados **no se
deshacen automáticamente** — solo se muestra una lista honesta de lo
que alcanzó a completarse. La reversión se hace a través del punto de
`git commit` guardado.

---

## Tests

```bash
pytest
```

---

## Estructura del proyecto

```
agent/       # núcleo: wrapper de LLM con fallback/reintentos, bucle del agente, memoria
tools/       # herramientas universales y agnósticas al lenguaje para archivos/texto
ui/          # interfaz de escritorio hecha con PySide6
assets/      # fuente, ícono
tests/       # pytest
main.py      # punto de entrada CLI
run_gui.py   # punto de entrada usado para compilar la GUI en un .exe
```

---

## Limitaciones conocidas

Este es un proyecto personal que funciona con niveles gratuitos, no un
producto comercial — las limitaciones de abajo son decisiones de diseño
conscientes, no errores:

- **Los límites de los proveedores gratuitos** son finitos. Si los tres
  proveedores están sobrecargados a la vez, el agente esperará y
  reintentará dos veces (hasta ~20 segundos), pero si eso no ayuda,
  informará un error y sugerirá intentarlo más tarde.
- **No hay reversión por archivo individual** durante una edición de
  varios pasos — solo reversión de la sesión completa a través de
  `git`, realizada por el usuario.
- **No lee capturas de pantalla ni imágenes** — los modelos gratuitos
  de la cadena actual de proveedores no soportan esto lo suficientemente
  bien.
- **No hace scraping masivo de sitios web** — deliberadamente, por la
  protección anti-bots y la inestabilidad ante cambios de maquetado.
  Puede leer el contenido de una sola página si se le da el enlace
  explícitamente.
- El trabajo visual y creativo (organización de escenas, elección de
  assets, decisiones de arquitectura) no lo hace el agente — eso sigue
  siendo tarea del desarrollador.

---

## Licencia

MIT
