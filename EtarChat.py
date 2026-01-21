import asyncio
import discord
import os
import time
from aiohttp import web  # <--- Substituímos 'websockets' por 'aiohttp' (Mais robusto)
from discord.ext import commands, tasks
from mcstatus import JavaServer
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env
load_dotenv()

# ================= CONFIGURAÇÃO GERAL =================

TOKEN = os.getenv("DISCORD_TOKEN")
# Pega a porta da Nuvem (Render/Discloud) ou usa 8080 se for local
WS_PORT = int(os.getenv("PORT", 8080))

COMMAND_CHANNEL_ID = 1463166986652614835  # Canal Admin (Único para todos)
SERVER_IMAGE = "https://i.imgur.com/jhYbb3a.png"
ANTI_SPAM_SECONDS = 2

# ================= ⚙️ CONFIGURAÇÃO DOS SERVIDORES =================
SERVIDORES = {
    "Cobblemon": {
        "nome": "Cobble",
        "ip": "elgae-sp1-m005.elgaehost.com.br",
        "port": 25571,
        "chat_channel": 1463186334549282888,
        "status_channel": 1463190910358520008
    },
    # Adicione outros servidores aqui...
}

# =================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix="!", intents=intents)

# Armazena conexões ativas: { "token_survival": websocket_response }
active_connections = {}
last_message_time = {}

# ================= FUNÇÕES AUXILIARES =================

async def get_mc_status(ip, port):
    """Obtém status de um servidor específico"""
    def _query():
        server = JavaServer(ip, port)
        try: return server.query(), server.status().latency
        except: return server.status(), server.status().latency
    try: return await asyncio.to_thread(_query)
    except: return None, None

async def enviar_para_servidor(token, payload):
    """Envia payload apenas para o servidor específico usando aiohttp"""
    ws = active_connections.get(token)
    if ws and not ws.closed:
        try:
            await ws.send_str(payload) # aiohttp usa send_str
            return True
        except:
            return False
    return False

# ================= WEBSOCKET HANDLER (AIOHTTP) =================

async def websocket_handler(request):
    """Gerencia a conexão do Minecraft e Chat"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    print(f"🔌 Nova conexão recebida: {request.remote}")
    server_token = None

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                message = msg.data

                # 1. Autenticação (Descobre qual servidor é)
                if message.startswith("AUTH|"):
                    token_recebido = message.split("|")[1]
                    
                    if token_recebido in SERVIDORES:
                        server_token = token_recebido
                        active_connections[server_token] = ws
                        nome = SERVIDORES[server_token]['nome']
                        print(f"✅ Servidor '{nome}' autenticado e conectado!")
                    else:
                        print(f"❌ Token desconhecido: {token_recebido}")
                        await ws.close()
                    continue
                
                if not server_token: continue

                # 2. Recebe Chat (Minecraft -> Discord)
                if message.startswith("CHAT_MC|"):
                    parts = message.split("|", 2)
                    if len(parts) >= 3:
                        _, player, text = parts
                        
                        channel_id = SERVIDORES[server_token]["chat_channel"]
                        channel = bot.get_channel(channel_id)
                        
                        if channel:
                            embed = discord.Embed(description=text, color=discord.Color.green())
                            embed.set_author(name=player, icon_url=f"https://mc-heads.net/avatar/{player}/64")
                            await channel.send(embed=embed)
            
            elif msg.type == web.WSMsgType.ERROR:
                print(f"⚠️ Erro na conexão WS: {ws.exception()}")

    finally:
        if server_token and server_token in active_connections:
            del active_connections[server_token]
            nome = SERVIDORES[server_token]['nome']
            print(f"ℹ️ Servidor '{nome}' desconectado.")

    return ws

# --- ROTA DE SAÚDE (CORREÇÃO PARA O RENDER/DISCLOUD) ---
async def health_check(request):
    """Responde 'OK' para os pings do Render, evitando o erro 500/Crash"""
    return web.Response(text="OK", status=200)

async def start_web_server():
    """Inicia o servidor web compatível com nuvem"""
    app = web.Application()
    
    # Adiciona as rotas:
    # '/' -> Aceita o WebSocket do Minecraft
    # '/healthz' e HEAD '/' -> Aceita o ping do Render
    app.add_routes([
        web.get('/', websocket_handler),
        web.get('/healthz', health_check),
        web.head('/', health_check) 
    ])
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # '0.0.0.0' é obrigatório para aceitar conexões externas na nuvem
    site = web.TCPSite(runner, '0.0.0.0', WS_PORT)
    await site.start()
    
    print(f"🚀 Servidor AIOHTTP rodando na porta {WS_PORT}")
    
    # Mantém esta tarefa rodando para sempre
    while True:
        await asyncio.sleep(3600)

# ================= STATUS LOOP (MULTI-SERVER) =================

@tasks.loop(seconds=60)
async def atualizar_status():
    for token, config in SERVIDORES.items():
        channel_id = config.get("status_channel")
        if not channel_id: continue
        
        channel = bot.get_channel(channel_id)
        if not channel: continue

        data, latency = await get_mc_status(config["ip"], config["port"])
        nome = config["nome"]

        if data:
            try:
                p_online = data.players.online
                p_max = data.players.max
                p_names = getattr(data.players, 'names', [])
            except: p_online = 0; p_max = 0; p_names = []

            embed = discord.Embed(title=f"🟢 {nome} Online", color=discord.Color.green())
            embed.add_field(name="Jogadores", value=f"{p_online}/{p_max}", inline=True)
            embed.add_field(name="Ping", value=f"{int(latency)} ms", inline=True)
            if p_names:
                embed.description = f"**Online:** {', '.join(p_names)[:1000]}"
        else:
            embed = discord.Embed(title=f"🔴 {nome} Offline", description="Servidor desligado.", color=discord.Color.red())
            embed.set_thumbnail(url=SERVER_IMAGE)
        
        embed.set_footer(text=f"Atualizado às {time.strftime('%H:%M:%S')}")

        messages = []
        async for msg in channel.history(limit=5):
            if msg.author == bot.user: messages.append(msg)
        
        if not messages: await channel.send(embed=embed)
        else:
            await messages[0].edit(embed=embed)
            if len(messages) > 1:
                for old in messages[1:]: await old.delete()

# ================= COMANDOS =================

@bot.command(aliases=['jogadores', 'online'])
async def player(ctx):
    server_config = None
    for token, config in SERVIDORES.items():
        if ctx.channel.id == config["chat_channel"]:
            server_config = config
            break
    
    if not server_config:
        if ctx.channel.id == COMMAND_CHANNEL_ID:
            msg = "**📊 Resumo Global:**\n"
            for token, config in SERVIDORES.items():
                data, _ = await get_mc_status(config["ip"], config["port"])
                if data: msg += f"✅ **{config['nome']}:** {data.players.online}/{data.players.max}\n"
                else: msg += f"🔴 **{config['nome']}:** Offline\n"
            await ctx.send(msg)
        return

    data, _ = await get_mc_status(server_config["ip"], server_config["port"])
    if data:
        names = getattr(data.players, 'names', []) or []
        count = f"{data.players.online}/{data.players.max}"
        msg = f"👥 **{server_config['nome']} Online ({count}):**\n{', '.join(names)}"
        await ctx.send(msg)
    else:
        await ctx.send(f"🔴 {server_config['nome']} está Offline.")

@bot.command()
@commands.has_permissions(administrator=True)
async def cmd(ctx, server_name: str = None, *, comando: str = None):
    if ctx.channel.id != COMMAND_CHANNEL_ID:
        await ctx.message.delete()
        return

    if not server_name or not comando:
        await ctx.send("❌ Uso: `!cmd <nome_server> <comando>`")
        return

    target_token = None
    for token, config in SERVIDORES.items():
        if config["nome"].lower() == server_name.lower():
            target_token = token
            break
    
    if target_token:
        payload = f"CONSOLE_CMD|{comando}"
        if await enviar_para_servidor(target_token, payload):
            await ctx.message.add_reaction("✅")
        else:
            await ctx.send(f"❌ {server_name} desconectado.")
    else:
        await ctx.send("❌ Servidor não encontrado.")

# ================= EVENTOS =================

@bot.event
async def on_ready():
    print(f"✅ Bot Multi-Server Online: {bot.user}")
    if not atualizar_status.is_running(): atualizar_status.start()

@bot.event
async def on_message(message):
    if message.author.bot: return

    target_token = None
    for token, config in SERVIDORES.items():
        if message.channel.id == config["chat_channel"]:
            target_token = token
            break
    
    if target_token and not message.content.startswith("!"):
        now = time.time()
        if now - last_message_time.get(message.author.id, 0) >= ANTI_SPAM_SECONDS:
            last_message_time[message.author.id] = now
            color_hex = str(message.author.color)
            payload = f"CHAT_DISCORD|{message.author.display_name}|{message.content}|{color_hex}"
            await enviar_para_servidor(target_token, payload)

    await bot.process_commands(message)

# ================= MAIN =================

async def main():
    if not TOKEN: 
        print("❌ Token não configurado.")
        return
    # Inicia o Servidor Web (WebSocket) e o Bot ao mesmo tempo
    await asyncio.gather(start_web_server(), bot.start(TOKEN))

if __name__ == "__main__":
    try: asyncio.run(main())

    except KeyboardInterrupt: pass

