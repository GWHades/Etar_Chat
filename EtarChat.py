import asyncio
import discord
import os
import time
import json  # <--- Necessário para ler o formato do Java
from aiohttp import web
from discord.ext import commands, tasks
from mcstatus import JavaServer
from dotenv import load_dotenv

load_dotenv()

# ================= CONFIGURAÇÃO =================

TOKEN = os.getenv("DISCORD_TOKEN")
WS_PORT = int(os.getenv("PORT", 8080))
COMMAND_CHANNEL_ID = 1463166986652614835
SERVER_IMAGE = "https://i.imgur.com/jhYbb3a.png"
ANTI_SPAM_SECONDS = 2

# IMPORTANTE: A chave deste dicionário deve ser EXATAMENTE a senha definida no mod Java
SERVIDORES = {
    "senha_padrao": {  # <--- Mude "senha_padrao" para o token que está no config do Mod
        "nome": "Cobblemon",
        "ip": "schools-chamber.gl.joinmc.link",
        "port": 25565,
        "chat_channel": 1463186334549282888,
        "status_channel": 1463190910358520008
    }
}

# =================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix="!", intents=intents)
active_connections = {}
last_message_time = {}

# ================= FUNÇÕES AUXILIARES =================

async def get_mc_status(ip, port):
    def _query():
        server = JavaServer(ip, port)
        try: return server.query(), server.status().latency
        except: return server.status(), server.status().latency
    try: return await asyncio.to_thread(_query)
    except: return None, None

async def enviar_para_servidor(token, json_payload):
    """Envia JSON para o servidor específico"""
    ws = active_connections.get(token)
    if ws and not ws.closed:
        try:
            # Converte o dicionário Python para string JSON antes de enviar
            await ws.send_str(json.dumps(json_payload))
            return True
        except:
            return False
    return False

# ================= WEBSOCKET HANDLER (JSON) =================

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    print(f"🔌 Conexão recebida: {request.remote}")
    server_token = None

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    # Tenta ler como JSON (O formato que o Java envia)
                    data = json.loads(msg.data)
                    msg_type = data.get("type")

                    # 1. Autenticação
                    if msg_type == "AUTH":
                        token_recebido = data.get("token")
                        
                        if token_recebido in SERVIDORES:
                            server_token = token_recebido
                            active_connections[server_token] = ws
                            nome = SERVIDORES[server_token]['nome']
                            print(f"✅ Servidor '{nome}' Autenticado!")
                        else:
                            print(f"❌ Token inválido: {token_recebido}")
                            await ws.close()
                        continue
                    
                    if not server_token: continue

                    # 2. Recebe Chat (Minecraft -> Discord)
                    if msg_type == "CHAT_MC":
                        player = data.get("user")
                        text = data.get("message")
                        
                        config = SERVIDORES[server_token]
                        channel = bot.get_channel(config["chat_channel"])
                        
                        if channel:
                            embed = discord.Embed(description=text, color=discord.Color.green())
                            embed.set_author(name=player, icon_url=f"https://mc-heads.net/avatar/{player}/64")
                            await channel.send(embed=embed)

                except json.JSONDecodeError:
                    print(f"⚠️ Erro: Recebido dados que não são JSON: {msg.data}")
                except Exception as e:
                    print(f"⚠️ Erro ao processar mensagem: {e}")

            elif msg.type == web.WSMsgType.ERROR:
                print(f"⚠️ Erro WS: {ws.exception()}")

    finally:
        if server_token and server_token in active_connections:
            del active_connections[server_token]
            nome = SERVIDORES.get(server_token, {}).get('nome', 'Desconhecido')
            print(f"ℹ️ Servidor '{nome}' desconectado.")

    return ws

# --- ROTAS ---
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    app = web.Application()
    app.add_routes([
        web.get('/', websocket_handler),
        web.get('/healthz', health_check),
        web.head('/', health_check) 
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WS_PORT)
    await site.start()
    print(f"🚀 Servidor Web rodando na porta {WS_PORT}")
    while True: await asyncio.sleep(3600)

# ================= STATUS LOOP =================

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

        try:
            messages = [msg async for msg in channel.history(limit=5) if msg.author == bot.user]
            if not messages: await channel.send(embed=embed)
            else:
                await messages[0].edit(embed=embed)
                if len(messages) > 1:
                    for old in messages[1:]: await old.delete()
        except Exception as e:
            print(f"Erro ao atualizar status: {e}")

# ================= COMANDOS =================

@bot.command()
async def player(ctx):
    # Lógica mantida igual, apenas adaptação se necessário
    server_config = None
    for token, config in SERVIDORES.items():
        if ctx.channel.id == config["chat_channel"]:
            server_config = config
            break
    
    if not server_config:
        await ctx.send("Canal não vinculado a um servidor.")
        return

    data, _ = await get_mc_status(server_config["ip"], server_config["port"])
    if data:
        names = getattr(data.players, 'names', []) or []
        msg = f"👥 **{server_config['nome']} Online ({data.players.online}/{data.players.max}):**\n{', '.join(names)}"
        await ctx.send(msg)
    else:
        await ctx.send(f"🔴 {server_config['nome']} está Offline.")

@bot.command()
@commands.has_permissions(administrator=True)
async def cmd(ctx, server_name: str = None, *, comando: str = None):
    if ctx.channel.id != COMMAND_CHANNEL_ID: return

    target_token = None
    for token, config in SERVIDORES.items():
        if config["nome"].lower() == server_name.lower():
            target_token = token
            break
    
    if target_token:
        # Mudança: Envia JSON, não String pura
        payload = {"type": "CONSOLE_CMD", "command": comando} # Obs: precisa implementar no Java se quiser usar
        # Como o Java atual só implementou CHAT_DISCORD, vamos focar nisso, 
        # mas se quiser comandos console, precisará atualizar o JavaHandler.
        
        # Por enquanto, comando não está no JavaHandler que fizemos,
        # mas a estrutura de envio seria essa.
        await ctx.send("⚠️ Comando de console ainda não implementado no Mod Java.")
    else:
        await ctx.send("❌ Servidor não encontrado.")

# ================= EVENTOS DISCORD =================

@bot.event
async def on_ready():
    print(f"✅ Bot Online: {bot.user}")
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
            
            # Mudança: Cria payload JSON
            payload = {
                "type": "CHAT_DISCORD",
                "user": message.author.display_name,
                "message": message.content
            }
            await enviar_para_servidor(target_token, payload)

    await bot.process_commands(message)

async def main():
    if not TOKEN: 
        print("❌ Token não configurado.")
        return
    await asyncio.gather(start_web_server(), bot.start(TOKEN))

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
