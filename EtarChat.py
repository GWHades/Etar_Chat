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

COMMAND_CHANNEL_ID = 1463166986652614835  # ID do canal para comandos de admin (!cmd)
SERVER_IMAGE = "https://i.imgur.com/jhYbb3a.png" # Imagem para o Embed Offline
ANTI_SPAM_SECONDS = 2

# ================= 🌍 CONFIGURAÇÃO DOS SERVIDORES =================
# A chave (ex: "senha_segura_123") deve ser IGUAL ao WEBSOCKET_TOKEN no config do Mod.
SERVIDORES = {
    "Cobblemon": {  
        "nome": "Cobblemon",
        "ip": "elgae-sp1-m005.elgaehost.com.br",
        "port": 25571,
        "chat_channel": 1463957173506801694,   # Canal onde sai o bate-papo
        "status_channel": 1463957324543688861  # Canal onde fica o Embed de Status
    },
    # Você pode adicionar mais servidores aqui...
}

# =================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix="!", intents=intents)

# Armazena conexões ativas: { "token_senha": websocket_object }
active_connections = {}
last_message_time = {}

# ================= 🛠️ FUNÇÕES AUXILIARES =================

async def get_mc_status(ip, port):
    """Consulta básica via MCStatus (Fallback)"""
    def _query():
        server = JavaServer(ip, port)
        try: return server.query(), server.status().latency
        except: return server.status(), server.status().latency
    try: return await asyncio.to_thread(_query)
    except: return None, None

async def enviar_para_servidor(token, json_payload):
    """Envia um JSON para o servidor Minecraft conectado"""
    ws = active_connections.get(token)
    if ws and not ws.closed:
        try:
            await ws.send_str(json.dumps(json_payload))
            return True
        except Exception as e:
            print(f"Erro ao enviar para {token}: {e}")
            return False
    return False

async def atualizar_embed_status(config, data):
    """Atualiza o canal de status com dados detalhados (TPS, RAM) vindos do Mod"""
    channel_id = config.get("status_channel")
    if not channel_id: return

    channel = bot.get_channel(channel_id)
    if not channel: return

    # Extrai dados do JSON enviado pelo Mod
    tps = float(data.get("tps", 20.0))
    ram_used = data.get("ram_used", 0)
    ram_max = data.get("ram_max", 0)
    uptime = data.get("uptime", "0h 0m")
    players = data.get("players", 0)
    max_players = data.get("max_players", 0)

    # Define cor baseada na performance (TPS)
    cor = discord.Color.green()
    estado = "Excelente"
    if tps < 18.0: 
        cor = discord.Color.orange()
        estado = "Instável"
    if tps < 15.0: 
        cor = discord.Color.red()
        estado = "Crítico (Lag)"

    embed = discord.Embed(title=f"📊 Status: {config['nome']}", color=cor)
    embed.add_field(name="📶 TPS / Performance", value=f"**{tps}** ({estado})", inline=True)
    embed.add_field(name="💾 RAM (Memória)", value=f"{ram_used}MB / {ram_max}MB", inline=True)
    embed.add_field(name="👥 Jogadores", value=f"{players}/{max_players}", inline=True)
    embed.add_field(name="⏱️ Tempo Online", value=f"{uptime}", inline=False)
    
    embed.set_thumbnail(url=SERVER_IMAGE)
    embed.set_footer(text=f"Última atualização: {time.strftime('%H:%M:%S')}")

    # Lógica de edição para evitar spam
    try:
        messages = [msg async for msg in channel.history(limit=5) if msg.author == bot.user]
        if not messages:
            await channel.send(embed=embed)
        else:
            await messages[0].edit(embed=embed)
            # Limpa mensagens duplicadas antigas se houver
            if len(messages) > 1:
                for old in messages[1:]: await old.delete()
    except Exception as e:
        print(f"Erro ao atualizar embed rico: {e}")

# ================= 🔌 WEBSOCKET HANDLER =================

async def websocket_handler(request):
    """Gerencia conexões recebidas do Minecraft"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    print(f"🔌 Nova conexão de: {request.remote}")
    server_token = None

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
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

                    # 3. Recebe Status Rico (Do Mod)
                    elif msg_type == "STATUS_UPDATE":
                        config = SERVIDORES.get(server_token)
                        if config:
                            await atualizar_embed_status(config, data)

                except json.JSONDecodeError:
                    print(f"⚠️ JSON Inválido recebido: {msg.data}")
                except Exception as e:
                    print(f"⚠️ Erro ao processar msg: {e}")

            elif msg.type == web.WSMsgType.ERROR:
                print(f"⚠️ Erro WS: {ws.exception()}")

    finally:
        if server_token and server_token in active_connections:
            del active_connections[server_token]
            nome = SERVIDORES.get(server_token, {}).get('nome', 'Desconhecido')
            print(f"ℹ️ Servidor '{nome}' desconectado.")
            
            # Opcional: Avisar no chat que o servidor caiu/fechou conexão
            # config = SERVIDORES.get(server_token)
            # if config:
            #     channel = bot.get_channel(config["status_channel"])
            #     if channel: await channel.send("🔴 Servidor desconectou do WebSocket.")

    return ws

# --- SERVIDOR WEB (AIOHTTP) ---
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
    print(f"🚀 Web Server rodando na porta {WS_PORT}")
    while True: await asyncio.sleep(3600)

# ================= 🔄 LOOP DE STATUS (FALLBACK) =================

@tasks.loop(seconds=60)
async def loop_status_fallback():
    """Loop secundário para detectar offline ou servidores sem o mod"""
    for token, config in SERVIDORES.items():
        # Se o servidor estiver conectado via WebSocket, deixamos o Mod atualizar o status (é mais preciso)
        if token in active_connections:
            continue

        # Se NÃO estiver conectado, usamos o método antigo para mostrar Offline ou status básico
        channel_id = config.get("status_channel")
        if not channel_id: continue
        channel = bot.get_channel(channel_id)
        if not channel: continue

        data, latency = await get_mc_status(config["ip"], config["port"])
        nome = config["nome"]

        if data:
            # Servidor online, mas sem mod de chat conectado
            try:
                p_online = data.players.online
                p_max = data.players.max
            except: p_online = 0; p_max = 0

            embed = discord.Embed(title=f"🟡 {nome} Online (Sem Chat)", color=discord.Color.gold())
            embed.add_field(name="Jogadores", value=f"{p_online}/{p_max}", inline=True)
            embed.add_field(name="Ping", value=f"{int(latency)} ms", inline=True)
            embed.set_footer(text="Conexão WebSocket: Desconectada")
        else:
            # Servidor Offline
            embed = discord.Embed(title=f"🔴 {nome} Offline", description="Servidor desligado.", color=discord.Color.red())
            embed.set_thumbnail(url=SERVER_IMAGE)
            embed.set_footer(text=f"Verificado às {time.strftime('%H:%M:%S')}")
        
        try:
            messages = [msg async for msg in channel.history(limit=5) if msg.author == bot.user]
            if not messages: await channel.send(embed=embed)
            else:
                await messages[0].edit(embed=embed)
                if len(messages) > 1:
                    for old in messages[1:]: await old.delete()
        except: pass

# ================= 💬 COMANDOS DISCORD =================

@bot.command()
async def player(ctx):
    # Procura qual servidor está vinculado a este canal
    server_config = None
    for token, config in SERVIDORES.items():
        if ctx.channel.id == config["chat_channel"]:
            server_config = config
            break
    
    if not server_config:
        await ctx.send("Este canal não está vinculado a nenhum servidor.")
        return

    # Tenta pegar lista via Query (MCStatus)
    data, _ = await get_mc_status(server_config["ip"], server_config["port"])
    if data:
        names = getattr(data.players, 'names', []) or []
        msg = f"👥 **{server_config['nome']} Online ({data.players.online}/{data.players.max}):**\n{', '.join(names)}"
        await ctx.send(msg)
    else:
        await ctx.send(f"🔴 {server_config['nome']} parece estar Offline.")

@bot.command()
@commands.has_permissions(administrator=True)
async def cmd(ctx, server_name: str = None, *, comando: str = None):
    if ctx.channel.id != COMMAND_CHANNEL_ID: return

    if not server_name or not comando:
        await ctx.send("❌ Uso: `!cmd <nome_server> <comando>`")
        return

    target_token = None
    for token, config in SERVIDORES.items():
        if config["nome"].lower() == server_name.lower():
            target_token = token
            break
    
    if target_token:
        # Envia comando via JSON (Necessita implementação futura no Mod)
        payload = {"type": "CONSOLE_CMD", "command": comando}
        enviado = await enviar_para_servidor(target_token, payload)
        
        if enviado:
            await ctx.message.add_reaction("✅")
        else:
            await ctx.send("❌ Servidor desconectado ou erro ao enviar.")
    else:
        await ctx.send("❌ Servidor não encontrado na config.")

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
        # Anti-spam simples
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
    # Roda servidor Web e Bot simultaneamente
    await asyncio.gather(start_web_server(), bot.start(TOKEN))

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass

