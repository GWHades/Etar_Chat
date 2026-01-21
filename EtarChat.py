import asyncio
import discord
import os
import time
from discord.ext import commands, tasks
from mcstatus import JavaServer
import websockets
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env
load_dotenv()

# ================= CONFIGURAÇÃO GERAL =================

TOKEN = os.getenv("DISCORD_TOKEN")
WS_PORT = int(os.getenv("WS_PORT", 8080))
COMMAND_CHANNEL_ID = 1463166986652614835  # Canal Admin (Único para todos)
SERVER_IMAGE = "https://i.imgur.com/jhYbb3a.png"
ANTI_SPAM_SECONDS = 2

# ================= ⚙️ CONFIGURAÇÃO DOS SERVIDORES =================
# O "TOKEN" (chave do dicionário) deve ser igual ao da config.yml do plugin Java.
# Configure aqui seus servidores:

SERVIDORES = {
    "token_survival": {
        "nome": "Survival",           # Nome para comandos e exibição
        "ip": "127.0.0.1",            # IP do servidor
        "port": 25565,                # Porta do Minecraft
        "chat_channel": 1463186334549282888,   # Canal de Chat deste servidor
        "status_channel": 1463190910358520008  # Canal de Status deste servidor
    },
    
    # Exemplo de segundo servidor (Descomente e edite para usar):
    # "token_rankup": {
    #     "nome": "RankUP",
    #     "ip": "192.168.1.50",
    #     "port": 25566,
    #     "chat_channel": 111111111111111111,
    #     "status_channel": 222222222222222222
    # },
}

# =================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix="!", intents=intents)

# Armazena os WebSockets: { "token_survival": websocket_object }
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
    """Envia payload apenas para o servidor específico"""
    ws = active_connections.get(token)
    if ws:
        try:
            await ws.send(payload)
            return True
        except:
            return False
    return False

# ================= WEBSOCKET HANDLER =================

async def websocket_handler(websocket):
    print(f"🔌 Nova conexão recebida: {websocket.remote_address}")
    server_token = None
    
    try:
        async for message in websocket:
            # 1. Autenticação (Descobre qual servidor é)
            if message.startswith("AUTH|"):
                token_recebido = message.split("|")[1]
                
                if token_recebido in SERVIDORES:
                    server_token = token_recebido
                    active_connections[server_token] = websocket
                    nome = SERVIDORES[server_token]['nome']
                    print(f"✅ Servidor '{nome}' autenticado e conectado!")
                else:
                    print(f"❌ Token desconhecido tentou conectar: {token_recebido}")
                    await websocket.close()
                continue
            
            if not server_token: continue

            # 2. Recebe Chat (Minecraft -> Discord)
            if message.startswith("CHAT_MC|"):
                parts = message.split("|", 2)
                if len(parts) >= 3:
                    _, player, text = parts
                    
                    # Pega o canal configurado para ESTE servidor
                    channel_id = SERVIDORES[server_token]["chat_channel"]
                    channel = bot.get_channel(channel_id)
                    
                    if channel:
                        embed = discord.Embed(description=text, color=discord.Color.green())
                        embed.set_author(name=player, icon_url=f"https://mc-heads.net/avatar/{player}/64")
                        await channel.send(embed=embed)

    except: pass
    finally:
        if server_token and server_token in active_connections:
            del active_connections[server_token]
            nome = SERVIDORES[server_token]['nome']
            print(f"ℹ️ Servidor '{nome}' desconectado.")

async def start_websocket():
    print(f"🚀 WebSocket Multi-Server na porta {WS_PORT}")
    async with websockets.serve(websocket_handler, "0.0.0.0", WS_PORT):
        await asyncio.Future()

# ================= STATUS LOOP (MULTI-SERVER) =================

@tasks.loop(seconds=60)
async def atualizar_status():
    # Itera sobre todos os servidores configurados
    for token, config in SERVIDORES.items():
        channel_id = config.get("status_channel")
        if not channel_id: continue
        
        channel = bot.get_channel(channel_id)
        if not channel: continue

        # Busca dados deste servidor específico
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
            embed = discord.Embed(title=f"🔴 {nome} Offline", description="Servidor desligado ou reiniciando.", color=discord.Color.red())
            embed.set_thumbnail(url=SERVER_IMAGE)
        
        embed.set_footer(text=f"Atualizado às {time.strftime('%H:%M:%S')}")

        # Lógica de Auto-Limpeza (Por canal)
        messages = []
        async for msg in channel.history(limit=5):
            if msg.author == bot.user: messages.append(msg)
        
        if not messages:
            await channel.send(embed=embed)
        else:
            await messages[0].edit(embed=embed)
            if len(messages) > 1:
                for old in messages[1:]: await old.delete()

# ================= COMANDOS =================

@bot.command(aliases=['jogadores', 'online'])
async def player(ctx):
    """Mostra jogadores (Auto-detecta o servidor pelo canal)"""
    # Tenta descobrir de qual servidor o comando veio
    server_config = None
    for token, config in SERVIDORES.items():
        if ctx.channel.id == config["chat_channel"]:
            server_config = config
            break
    
    if not server_config:
        # Se usado fora de um canal de chat, mostra de TODOS (resumido)
        if ctx.channel.id == COMMAND_CHANNEL_ID:
            msg = "**📊 Resumo Global:**\n"
            for token, config in SERVIDORES.items():
                data, _ = await get_mc_status(config["ip"], config["port"])
                if data: msg += f"✅ **{config['nome']}:** {data.players.online}/{data.players.max}\n"
                else: msg += f"🔴 **{config['nome']}:** Offline\n"
            await ctx.send(msg)
        return

    # Se usado no canal correto, mostra detalhes
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
    """
    Uso: !cmd <nome_do_servidor> <comando>
    Exemplo: !cmd survival time set day
    """
    if ctx.channel.id != COMMAND_CHANNEL_ID:
        await ctx.message.delete()
        return

    if not server_name or not comando:
        await ctx.send("❌ Uso correto: `!cmd <nome_do_servidor> <comando>`\nEx: `!cmd survival say Ola`")
        return

    # Procura o token baseado no nome (Case insensitive)
    target_token = None
    for token, config in SERVIDORES.items():
        if config["nome"].lower() == server_name.lower():
            target_token = token
            break
    
    if not target_token:
        valid_names = [cfg['nome'] for cfg in SERVIDORES.values()]
        await ctx.send(f"❌ Servidor '{server_name}' não encontrado. Disponíveis: {', '.join(valid_names)}")
        return

    payload = f"CONSOLE_CMD|{comando}"
    if await enviar_para_servidor(target_token, payload):
        await ctx.message.add_reaction("✅")
    else:
        await ctx.send(f"❌ O servidor **{server_name}** não está conectado ao Bot.")

# ================= EVENTOS =================

@bot.event
async def on_ready():
    print(f"✅ Bot Multi-Server Online: {bot.user}")
    if not atualizar_status.is_running(): atualizar_status.start()

@bot.event
async def on_message(message):
    if message.author.bot: return

    # Verifica se a mensagem veio de algum canal de chat configurado
    target_token = None
    for token, config in SERVIDORES.items():
        if message.channel.id == config["chat_channel"]:
            target_token = token
            break
    
    # Se encontrou o servidor e não é comando, envia
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
    if not TOKEN: return
    await asyncio.gather(start_websocket(), bot.start(TOKEN))

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass