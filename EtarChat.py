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

COMMAND_CHANNEL_ID = 1463959802127454312  # ID do canal para comandos de admin (!cmd)
SERVER_IMAGE = "https://i.imgur.com/jhYbb3a.png" # Imagem para o Embed Offline
ANTI_SPAM_SECONDS = 2

# ================= 🌍 CONFIGURAÇÃO DOS SERVIDORES =================
# ATENÇÃO: A chave (ex: "SuaSenhaSegura123") deve ser IGUAL ao TOKEN/SENHA no config do Mod.
SERVIDORES = {
    "Cobblemon": {   # <--- AQUI DEVE SER A SENHA/TOKEN, NÃO O NOME "Cobblemon" SE A SENHA FOR DIFERENTE
        "nome": "Cobblemon",
        "ip": "elgae-sp1-m005.elgaehost.com.br",
        "port": 25571,
        "chat_channel": 1463957173506801694,   # Canal onde sai o bate-papo
        "status_channel": 1463957324543688861  # Canal onde fica o Embed de Status
    },
    # Adicione outros servidores aqui se necessário
}

# =================================================================

intents = discord.Intents.default()
intents.message_content = True # Obrigatório estar ativado no Developer Portal também
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
            print(f"❌ Erro ao enviar para {token}: {e}")
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
    # ram_used = data.get("ram_used", 0) # Não usado no embed atual, mas disponível
    # ram_max = data.get("ram_max", 0)
    uptime = data.get("uptime", "0h 0m")
    players = data.get("players", 0)
    max_players = data.get("max_players", 0)

    # Define cor baseada na performance (TPS)
    cor = discord.Color.green()
    if tps < 18.0: 
        cor = discord.Color.orange()
    if tps < 15.0: 
        cor = discord.Color.red()

    embed = discord.Embed(title=f"📊 Status: {config['nome']}", color=cor)
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
        print(f"⚠️ Erro ao atualizar embed rico: {e}")

# ================= 🔌 WEBSOCKET HANDLER =================

async def websocket_handler(request):
    """Gerencia conexões recebidas do Minecraft"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    print(f"🔌 Tentativa de conexão de: {request.remote}")
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
                        # Verifica se o token recebido existe nas chaves do dicionário SERVIDORES
                        if token_recebido in SERVIDORES:
                            server_token = token_recebido
                            active_connections[server_token] = ws
                            nome = SERVIDORES[server_token]['nome']
                            print(f"✅ Servidor '{nome}' Autenticado com sucesso!")
                        else:
                            print(f"❌ Token inválido recebido: {token_recebido}")
                            await ws.close()
                        continue
                    
                    if not server_token: continue

                    # 2. Recebe Chat (Minecraft -> Discord)
                    if msg_type == "CHAT_MC":
                        player = data.get("user")
                        text = data.get("message")
                        print(f"💬 MC -> Discord ({SERVIDORES[server_token]['nome']}): {player}: {text}") # Debug
                        
                        config = SERVIDORES[server_token]
                        channel = bot.get_channel(config["chat_channel"])
                        if channel:
                            embed = discord.Embed(description=text, color=discord.Color.green())
                            embed.set_author(name=player, icon_url=f"https://mc-heads.net/avatar/{player}/64")
                            await channel.send(embed=embed)
                        else:
                            print(f"⚠️ Canal de chat não encontrado para {config['nome']}")

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
    # Loop infinito para manter o servidor web rodando
    while True:
        await asyncio.sleep(3600)

# ================= 🔄 LOOP DE STATUS (FALLBACK) =================

@tasks.loop(seconds=60)
async def loop_status_fallback():
    """Loop secundário para detectar offline ou servidores sem o mod"""
    for token, config in SERVIDORES.items():
        # Se estiver conectado via WebSocket, pulamos (o Mod atualiza)
        if token in active_connections:
            continue

        channel_id = config.get("status_channel")
        if not channel_id: continue
        channel = bot.get_channel(channel_id)
        if not channel: continue

        data, latency = await get_mc_status(config["ip"], config["port"])
        nome = config["nome"]

        if data:
            # Servidor online, mas sem mod conectado
            try:
                p_online = data.players.online
                p_max = data.players.max
            except: p_online = 0; p_max = 0

            embed = discord.Embed(title=f"🟡 {nome} Online (Sem Chat)", color=discord.Color.gold())
            embed.add_field(name="Jogadores", value=f"{p_online}/{p_max}", inline=True)
            embed.add_field(name="Ping", value=f"{int(latency)} ms", inline=True)
            embed.set_footer(text="Conexão WebSocket: Desconectada (Chat Inativo)")
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
    server_config = None
    for token, config in SERVIDORES.items():
        if ctx.channel.id == config["chat_channel"]:
            server_config = config
            break
    
    if not server_config:
        await ctx.send("Este canal não está vinculado a nenhum servidor.")
        return

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
    # Busca o token pelo nome do servidor
    for token, config in SERVIDORES.items():
        if config["nome"].lower() == server_name.lower():
            target_token = token
            break
    
    if target_token:
        # Envia comando via JSON
        payload = {"type": "CONSOLE_CMD", "command": comando}
        enviado = await enviar_para_servidor(target_token, payload)
        
        if enviado:
            await ctx.message.add_reaction("✅")
        else:
            await ctx.send("❌ Servidor desconectado ou erro ao enviar.")
    else:
        await ctx.send("❌ Servidor não encontrado na config.")

# ================= 🚀 EVENTOS & INICIALIZAÇÃO =================

@bot.event
async def on_ready():
    print(f"✅ Bot Online no Discord como: {bot.user}")
    print("⏳ Iniciando loops...")
    if not loop_status_fallback.is_running():
        loop_status_fallback.start()

@bot.event
async def on_message(message):
    if message.author.bot: return

    # --- DEBUG START: Apague isso depois que funcionar ---
    print(f"📩 MSG Recebida no Discord: '{message.content}' no canal {message.channel.id}")
    # --- DEBUG END ---

    target_token = None
    # Procura qual servidor pertence a este canal
    for token, config in SERVIDORES.items():
        if message.channel.id == config["chat_channel"]:
            target_token = token
            break
    
    # Se encontrou um servidor vinculado e NÃO é comando
    if target_token and not message.content.startswith("!"):
        print(f"🔎 Canal vinculado ao servidor: {SERVIDORES[target_token]['nome']}")
        
        if target_token in active_connections:
            # Anti-spam
            now = time.time()
            if now - last_message_time.get(message.author.id, 0) >= ANTI_SPAM_SECONDS:
                last_message_time[message.author.id] = now
                
                payload = {
                    "type": "CHAT_DISCORD",
                    "user": message.author.display_name,
                    "message": message.content
                }
                sucesso = await enviar_para_servidor(target_token, payload)
                if sucesso:
                    print("📤 Enviado para o Minecraft via WebSocket!")
                else:
                    print("❌ Falha ao enviar para o WebSocket (erro interno).")
            else:
                print("⏳ Mensagem ignorada pelo Anti-Spam.")
        else:
            print(f"❌ O servidor '{SERVIDORES[target_token]['nome']}' NÃO está conectado ao WebSocket.")
            # Opcional: Avisar no Discord que o server não está ouvindo
            # await message.add_reaction("🔌") 

    await bot.process_commands(message)

async def main():
    if not TOKEN: 
        print("❌ ERRO CRÍTICO: Token do Discord não configurado no .env")
        return
    
    print("🚀 Iniciando sistema...")
    # Roda servidor Web e Bot simultaneamente
    await asyncio.gather(start_web_server(), bot.start(TOKEN))

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass




