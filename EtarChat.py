import asyncio
import discord
import os
import time
import json
from aiohttp import web
from discord.ext import commands, tasks
from mcstatus import JavaServer
from dotenv import load_dotenv

# Carrega o .env (TOKEN e PORT)
load_dotenv()

# ================= ⚙️ CONFIGURAÇÃO GERAL =================

TOKEN = os.getenv("DISCORD_TOKEN")
# Porta do servidor Web (Pega do ambiente ou usa 8080 local)
WS_PORT = int(os.getenv("PORT", 8080))

COMMAND_CHANNEL_ID = 1463959802127454312
SERVER_IMAGE = "https://i.imgur.com/jhYbb3a.png"
ANTI_SPAM_SECONDS = 0.5

# --- 🔒 LISTA DE CARGOS PERMITIDOS ---
# Coloque aqui os IDs dos cargos do Discord que podem usar o !cmd
# (Administradores do servidor têm permissão automática)
ALLOWED_ROLE_IDS = [
    1372562722285162508,  # dev
    1372562830947258388,  # Exemplo: admin
    1386368759479926804, # header
    # Adicione quantos quiser...
]

# ================= 🌍 CONFIGURAÇÃO DOS SERVIDORES =================
SERVIDORES = {
    "Cobblemon": {
        "nome": "Cobblemon",
        "ip": "elgae-sp1-m005.elgaehost.com.br",
        "port": 25571,
        "chat_channel": 1463957173506801694,
        "status_channel": 1463957324543688861
    },
    "teste": {
        "nome": "teste",
        "ip": "127.0.0.1",
        "port": 25565,
        "chat_channel": 1463186334549282888,
        "status_channel": 1463190910358520008
    },
}

# =================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix="!", intents=intents)

active_connections = {}
last_message_time = {}

# ================= 🛠️ FUNÇÕES AUXILIARES =================

async def get_mc_status(ip, port):
    def _query():
        server = JavaServer(ip, port)
        try: return server.query(), server.status().latency
        except: return server.status(), server.status().latency
    try: return await asyncio.to_thread(_query)
    except: return None, None

async def enviar_para_servidor(token, json_payload):
    ws = active_connections.get(token)
    if ws and not ws.closed:
        try:
            await ws.send_str(json.dumps(json_payload))
            return True
        except Exception as e:
            print(f"❌ Erro ao enviar para {token}: {e}")
            return False
    return False

async def atualizar_embed_status(config, data):
    channel_id = config.get("status_channel")
    if not channel_id: return

    channel = bot.get_channel(channel_id)
    if not channel: return

    tps = float(data.get("tps", 20.0))
    uptime = data.get("uptime", "0h 0m")
    players = data.get("players", 0)
    max_players = data.get("max_players", 0)

    cor = discord.Color.green()
    if tps < 18.0: cor = discord.Color.orange()
    if tps < 15.0: cor = discord.Color.red()

    embed = discord.Embed(title=f"📊 Status: {config['nome']}", color=cor)
    embed.add_field(name="👥 Jogadores", value=f"{players}/{max_players}", inline=True)
    embed.add_field(name="⏱️ Tempo Online", value=f"{uptime}", inline=False)
    embed.add_field(name="📈 TPS", value=f"{tps}", inline=True)
    
    embed.set_thumbnail(url=SERVER_IMAGE)
    embed.set_footer(text=f"Última atualização: {time.strftime('%H:%M:%S')}")

    try:
        messages = [msg async for msg in channel.history(limit=5) if msg.author == bot.user]
        if not messages: await channel.send(embed=embed)
        else:
            await messages[0].edit(embed=embed)
            if len(messages) > 1:
                for old in messages[1:]: await old.delete()
    except Exception as e:
        print(f"⚠️ Erro ao atualizar embed: {e}")

# ================= 🔌 WEBSOCKET HANDLER =================

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    server_token = None

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")

                    if msg_type == "AUTH":
                        token_recebido = data.get("token")
                        if token_recebido in SERVIDORES:
                            server_token = token_recebido
                            active_connections[server_token] = ws
                            nome = SERVIDORES[server_token]['nome']
                            print(f"✅ Servidor '{nome}' CONECTADO!")
                        else:
                            print(f"❌ Token desconhecido: {token_recebido}")
                            await ws.close()
                        continue
                    
                    if not server_token: continue

                    if msg_type == "CHAT_MC":
                        player = data.get("user")
                        text = data.get("message")
                        config = SERVIDORES[server_token]
                        channel = bot.get_channel(config["chat_channel"])
                        if channel:
                            embed = discord.Embed(description=text, color=discord.Color.green())
                            embed.set_author(name=player, icon_url=f"https://mc-heads.net/avatar/{player}/64")
                            await channel.send(embed=embed)

                    elif msg_type == "STATUS_UPDATE":
                        config = SERVIDORES.get(server_token)
                        if config: await atualizar_embed_status(config, data)

                except Exception as e:
                    print(f"⚠️ Erro processando MSG: {e}")

            elif msg.type == web.WSMsgType.ERROR:
                print(f"⚠️ Erro WS: {ws.exception()}")
    finally:
        if server_token and server_token in active_connections:
            del active_connections[server_token]
            print(f"ℹ️ Servidor desconectado.")
    return ws

# --- SERVIDOR WEB ---
async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', websocket_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WS_PORT)
    await site.start()
    print(f"🚀 Web Server rodando na porta {WS_PORT}")
    while True: await asyncio.sleep(3600)

# ================= 🔄 LOOP DE STATUS =================

@tasks.loop(seconds=60)
async def loop_status_fallback():
    for token, config in SERVIDORES.items():
        if token in active_connections: continue

        channel_id = config.get("status_channel")
        if not channel_id: continue
        channel = bot.get_channel(channel_id)
        if not channel: continue

        data, _ = await get_mc_status(config["ip"], config["port"])
        nome = config["nome"]

        if data:
            embed = discord.Embed(title=f"🟡 {nome} Online (Sem Chat)", color=discord.Color.gold())
            embed.set_footer(text="Conexão WebSocket: Desconectada")
        else:
            embed = discord.Embed(title=f"🔴 {nome} Offline", description="Servidor desligado.", color=discord.Color.red())
            embed.set_footer(text=f"Verificado às {time.strftime('%H:%M:%S')}")
        
        try:
            messages = [msg async for msg in channel.history(limit=5) if msg.author == bot.user]
            if not messages: await channel.send(embed=embed)
            else: await messages[0].edit(embed=embed)
        except: pass

# ================= 💬 COMANDOS DISCORD =================

@bot.command()
async def player(ctx):
    server_config = None
    for token, config in SERVIDORES.items():
        if ctx.channel.id == config["chat_channel"]:
            server_config = config
            break
    
    if not server_config:
        await ctx.send("❌ Use este comando no canal de chat de um servidor.")
        return

    data, _ = await get_mc_status(server_config["ip"], server_config["port"])
    if data:
        names = getattr(data.players, 'names', []) or []
        msg = f"👥 **{server_config['nome']} Online ({data.players.online}/{data.players.max}):**\n{', '.join(names)}"
        await ctx.send(msg)
    else:
        await ctx.send(f"🔴 {server_config['nome']} parece estar Offline.")

# --- COMANDO CMD ATUALIZADO (VERIFICA CARGOS) ---
@bot.command()
async def cmd(ctx, arg1: str = None, *, arg2: str = None):
    # 1. Verificação de Permissão (Admin OU Cargo Permitido)
    is_admin = ctx.author.guild_permissions.administrator
    has_allowed_role = any(role.id in ALLOWED_ROLE_IDS for role in ctx.author.roles)

    if not (is_admin or has_allowed_role):
        await ctx.send("⛔ **Acesso Negado:** Você não tem permissão para usar este comando.")
        return

    # 2. Lógica do Comando
    target_token = None
    comando = None

    for token, config in SERVIDORES.items():
        if ctx.channel.id == config["chat_channel"]:
            target_token = token
            if arg1: comando = f"{arg1} {arg2}" if arg2 else arg1
            break
    
    if not target_token and arg1 and arg2:
        for token, config in SERVIDORES.items():
            if config["nome"].lower() == arg1.lower():
                target_token = token
                comando = arg2
                break
    
    if target_token and comando:
        payload = {"type": "CONSOLE_CMD", "command": comando, "user": ctx.author.display_name}
        enviado = await enviar_para_servidor(target_token, payload)
        
        if enviado:
            await ctx.message.add_reaction("✅")
            print(f"👑 Staff {ctx.author} executou: {comando}")
        else:
            await ctx.send("❌ Servidor desconectado do WebSocket.")
    else:
        await ctx.send("⚠️ **Uso incorreto ou servidor não encontrado.**\n`!cmd <comando>` (no chat) ou `!cmd <NomeServidor> <comando>`")

# ================= 🚀 INICIALIZAÇÃO =================

@bot.event
async def on_ready():
    print(f"✅ Bot Online: {bot.user}")
    if not loop_status_fallback.is_running():
        loop_status_fallback.start()

@bot.event
async def on_message(message):
    if message.author.bot: return

    # Verifica se a mensagem veio de um canal de chat configurado
    target_token = None
    for token, config in SERVIDORES.items():
        if message.channel.id == config["chat_channel"]:
            target_token = token
            break
    
    # Se for mensagem de chat (e não comando), envia para o Minecraft
    if target_token and not message.content.startswith("!"):
        now = time.time()
        if now - last_message_time.get(message.author.id, 0) >= ANTI_SPAM_SECONDS:
            last_message_time[message.author.id] = now
            payload = {
                "type": "CHAT_DISCORD",
                "user": message.author.display_name,
                "message": message.content
            }
            await enviar_para_servidor(target_token, payload)

    await bot.process_commands(message)

async def main():
    if not TOKEN: 
        print("❌ ERRO: Token do Discord não configurado no .env")
        return
    await asyncio.gather(start_web_server(), bot.start(TOKEN))

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try: asyncio.run(main())
    except KeyboardInterrupt: pass


