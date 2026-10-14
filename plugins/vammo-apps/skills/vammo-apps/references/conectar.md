# Cómo conectar Vammo Apps

La dirección del conector es **`https://api.apps.vammo.com/mcp`**. Al
conectar se abre el login de Vammo (la misma cuenta del Work Tracker) y una
pantalla que pide permiso: tocá **Permitir**. Después se puede desconectar
desde el portal (**Apps conectadas**).

## claude.ai (web, Desktop y celular)

1. Abrí **Configuración → Conectores** (en planes Team/Enterprise lo agrega
   una persona administradora para toda la organización).
2. **Agregar conector personalizado** → nombre `Vammo Apps`, URL
   `https://api.apps.vammo.com/mcp` → Agregar.
3. Tocá **Conectar**, iniciá sesión con tu cuenta de Vammo y tocá **Permitir**.
4. En una conversación nueva, activá el conector desde el menú de
   herramientas y escribí, por ejemplo: "Quiero armar una app en Vammo Apps
   para…".

Claude Desktop usa los mismos conectores de tu cuenta de claude.ai: no hay
que configurar nada más.

## Claude Code

```
claude mcp add --transport http vammo-apps https://api.apps.vammo.com/mcp
```

Después, dentro de Claude Code, `/mcp` → `vammo-apps` → **Authenticate**
(abre el navegador para el login y el permiso).

## ChatGPT

Necesita un plan con conectores personalizados (modo desarrollador).

1. **Configuración → Apps y conectores → Configuración avanzada** → activá
   el **modo desarrollador**.
2. **Crear** → nombre `Vammo Apps`, URL `https://api.apps.vammo.com/mcp`,
   autenticación **OAuth** → aceptá y conectá.
3. Iniciá sesión con tu cuenta de Vammo y tocá **Permitir**.
4. En el chat, elegí el conector Vammo Apps desde el menú **+**.

Para un GPT o un Proyecto de ChatGPT con las instrucciones de Vammo Apps,
ver `integrations/chatgpt/instrucciones.md`.

## Si algo no anda

- "No se pudo conectar": volvé a agregar el conector (el registro venció).
- "Cuenta no habilitada": tenés que entrar con tu cuenta de la empresa.
- Para desconectar: portal → **Apps conectadas** → Desconectar.
