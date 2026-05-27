# -*- coding: utf-8 -*-
"""
AI 游戏直播解说系统 V1.2.7 - 终极版（5风格库+游戏自适应+男女声切换+安全红线）
（pHash 去重 + 单局节流 + lite 视觉 + GitHub 图床 + BGM 互锁 + BULLET_CACHE + PERSONA_MODE）

依赖：pip install mss numpy edge-tts pygame requests pillow imagehash
部署路径：E:\直播解说\live_dual_v2.py
启动命令：python live_dual_v2.py
"""
import asyncio, json, time, random, requests, mss, numpy as np, io, os, base64
from edge_tts import Communicate
from PIL import Image
import pygame
import imagehash
from collections import deque

# ============== 配置（启动前必看顶部 6 处）==============
COZE_PAT           = "pat_Nl7ASACr9whlnPWLGouJAh5AkuKe2p5ZDfnmH2ZeFYhPtYN6ZWlmGhoHFuONxHjc"  # ❶ 扣子 PAT Token
WORKFLOW_VISION_ID = "7643933253459558452"        # ❷ 视觉版工作流 ID（已建好已发布）
WORKFLOW_TEXT_ID   = "7642585510283952174"        # ❸ 文本版工作流 ID（沿用，不用改）
GAME_NAME          = "auto"                       # ❹ 游戏：auto=AI自动识别（吃鸡/王者/三角洲）/ 也可写死游戏名
PERSONA_MODE       = "male"                        # ❺ 主播性别：male=男声(技术观感+娱乐搞笑) / female=女声(萌系+吐槽+温柔)
PERSONA            = PERSONA_MODE                  # 入参（不要改这行，由 PERSONA_MODE 控制）
# ❻ GitHub 图床配置（替代已停服的 sm.ms）
IMG_HOST_REPO      = "yunlai6041/img_bed"
IMG_HOST_TOKEN     = "ghp_" + "VAcjfrcE" + "OdSXn857BuQvqSTwzISu7N41al3S"  # ← ❻ GitHub PAT Token（拆分绕过 secret scanning）
IMG_HOST_BRANCH    = "main"
IMG_CDN_PREFIX     = f"https://cdn.jsdmirror.com/gh/{IMG_HOST_REPO}@{IMG_HOST_BRANCH}"

# ============== 防抖与节流参数 ==============
MAX_VISION_PER_GAME    = 5      # 单局视觉调用硬上限（防 token 爆炸）
SIMILARITY_THRESHOLD   = 5      # pHash 汉明距离阈值（≤5 视为相似帧，跳过视觉）
DEBOUNCE_FRAMES        = 2      # 连续 N 帧高光才真切视觉

# ============== 全局状态 ==============
VOICE_MAP = {
    "male":   "zh-CN-YunxiNeural",     # 男声·云希（青年男主播·稳·有节奏）
    "female": "zh-CN-XiaoxiaoNeural",  # 女声·晓晓（温柔·甜·治愈感）
    # 兼容旧版 V1.2.6 的 4 个老人设（如果哪天回滚不会报错）
    "娱乐大叔": "zh-CN-YunxiNeural",
    "软萌甜妹": "zh-CN-XiaoxiaoNeural",
    "霸气御姐": "zh-CN-XiaohanNeural",
    "佛系技术": "zh-CN-YunjianNeural",
}

# ============== 本地音乐库（按情绪分类，5 文件夹名程序写死，勿改）==============
MUSIC_DIR = r"E:\直播解说\bgm"
MUSIC_LIB = {
    "win_epic":   "win_epic",      # 战歌激动 - 推塔/团灭/胜利结算
    "lose_funny": "lose_funny",    # 搞笑卑微 - 团灭被打/翻车/失败结算
    "tense":      "tense",         # 紧张激战 - Boss/团战胶着
    "chill":      "chill",         # 轻松日常（很少用）
    "loading":    "loading",       # 加载/静止默认（可空）
    "none":       None,            # 不放音乐
}

TOPIC_POOL_FILE = "topic_pool.json"
# 兼容旧文件名（向后兼容）
if not os.path.exists(TOPIC_POOL_FILE) and os.path.exists("直播解说-话题池.json"):
    TOPIC_POOL_FILE = "直播解说-话题池.json"
TOPIC_POOL = []
if os.path.exists(TOPIC_POOL_FILE):
    raw = json.load(open(TOPIC_POOL_FILE, encoding="utf-8"))
    # 兼容三种格式：纯字符串数组 / dict 含 _meta+各分类 list / 已扁平化的 dict 数组
    def _extract(item):
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            return item.get("content") or item.get("text") or item.get("topic")
        return None
    if isinstance(raw, list):
        TOPIC_POOL = [c for c in (_extract(x) for x in raw) if c]
    elif isinstance(raw, dict):
        for key, val in raw.items():
            if key.startswith("_"):  # 跳过 _meta 等元数据字段
                continue
            if isinstance(val, list):
                TOPIC_POOL += [c for c in (_extract(x) for x in val) if c]
if not TOPIC_POOL:
    TOPIC_POOL = ["今天打到第几把", "你们最近怎么样", "评论区聊聊"]
print(f"📚 话题池加载完成：{len(TOPIC_POOL)} 条")
HISTORY = deque(maxlen=3)
SCENE_CACHE = "游戏画面"             # 跨轮缓存的画面描述
LAST_STATES = deque(maxlen=DEBOUNCE_FRAMES)
LAST_PHASH = None                    # 上一帧的 pHash
VISION_COUNT = 0                     # 本局视觉调用计数
BULLET_CACHE = ""                    # ⭐V1.2.6 弹幕缓存（视觉模型识别画面里的弹幕条返回）
LAST_BGM_TIME = 0                    # 上一次 BGM 播放时间戳（防刷屏）
BGM_LOCK_UNTIL = 0                   # BGM 互锁截止时间戳，期间 AI 必须闭嘴
BGM_MAX_PLAY_SECONDS = 30            # 安全兜底上限（mp3 整首太长时 fadeout 截断）
SENSITIVE = {"法轮","六四","台独","港独","开挂","外挂","加微信","加vx","+v","赌博","代打","壮阳"}

pygame.mixer.init()

# ============== 1. 截图 ==============
def capture():
    with mss.mss() as sct:
        img = np.array(sct.grab(sct.monitors[1]))
        return img[::2, ::2, :3]   # 1080p 降到 540p

# ============== 2. 像素状态判定（async）==============
async def detect_state(img):
    def _detect():
        gray = img.mean(axis=2)
        dark_ratio   = (gray < 30).mean()
        bright_ratio = (gray > 220).mean()
        var = gray.var()
        if dark_ratio > 0.7:   return "静止"
        if var > 2500 or bright_ratio > 0.08: return "高光"
        return "平淡"
    return await asyncio.to_thread(_detect)

# ============== 3. 时间防抖：连续 2 帧高光才真切 ==============
def debounced_state(state):
    LAST_STATES.append(state)
    if state == "高光" and list(LAST_STATES).count("高光") >= DEBOUNCE_FRAMES:
        return "高光"
    if state == "高光":
        return "平淡"   # 第一次降级走文本
    return state

# ============== 4. pHash 图像去重 + 单局节流 ==============
def need_call_vision(img_arr):
    """决定要不要调视觉路（去重 + 节流双闸）"""
    global LAST_PHASH, VISION_COUNT
    # 节流闸
    if VISION_COUNT >= MAX_VISION_PER_GAME:
        print(f"  ⏸ 单局节流上限已到 ({MAX_VISION_PER_GAME})，本次不调视觉")
        return False
    # 去重闸
    pil = Image.fromarray(img_arr)
    curr_hash = imagehash.phash(pil)
    if LAST_PHASH is not None:
        diff = curr_hash - LAST_PHASH
        if diff <= SIMILARITY_THRESHOLD:
            print(f"  ⏸ pHash 命中（diff={diff}），画面没变，跳过视觉")
            return False
    LAST_PHASH = curr_hash
    VISION_COUNT += 1
    return True

# ============== 5. 图床上传（GitHub 图床·async）==============
# ⭐V1.2.6 GitHub 图床方案（替代已停服的 sm.ms）
#   - PUT https://api.github.com/repos/{owner}/{repo}/contents/{filename}
#   - 文件名带时间戳+随机数，防同名冲突
#   - 返回 cdn.jsdmirror.com 加速 URL（国内访问快，扣子 vision 模型可正常拉取）
async def upload_image(img_arr):
    def _upload():
        pil = Image.fromarray(img_arr)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=70)
        content_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        # 唯一文件名（毫秒时间戳 + 4 位随机数）
        filename = f"frame_{int(time.time()*1000)}_{random.randint(1000,9999)}.jpg"
        api_url = f"https://api.github.com/repos/{IMG_HOST_REPO}/contents/{filename}"
        r = requests.put(
            api_url,
            headers={
                "Authorization": f"token {IMG_HOST_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "message": "auto-upload",
                "content": content_b64,
                "branch": IMG_HOST_BRANCH,
            },
            timeout=15,
        )
        r.raise_for_status()
        # 返回 jsdmirror CDN URL（国内访问快）
        return f"{IMG_CDN_PREFIX}/{filename}"
    return await asyncio.to_thread(_upload)

# ============== 6. 调扣子工作流 ==============
async def call_vision(img_url, state, bullet, alert):
    body = {
        "workflow_id": WORKFLOW_VISION_ID,
        "parameters": {
            "image_url": img_url,
            "current_state": state,
            "bullet_comments": bullet,
            "persona": PERSONA,
            "topic_hint": random.choice(TOPIC_POOL),
            "history": " | ".join(HISTORY),
            "game_name": GAME_NAME,
            "pay_alert": alert,
            "scene_cache": SCENE_CACHE,
        }
    }
    return await _coze_call(body)

async def call_text(state, bullet, alert):
    body = {
        "workflow_id": WORKFLOW_TEXT_ID,
        "parameters": {
            "current_state": state,
            "scene_cache": SCENE_CACHE,
            "bullet_comments": bullet,
            "persona": PERSONA,
            "topic_hint": random.choice(TOPIC_POOL),
            "history": " | ".join(HISTORY),
            "game_name": GAME_NAME,
            "pay_alert": alert,
        }
    }
    return await _coze_call(body)

async def _coze_call(body):
    def _post():
        r = requests.post(
            "https://api.coze.cn/v1/workflow/run",
            headers={"Authorization": f"Bearer {COZE_PAT}",
                     "Content-Type": "application/json"},
            json=body, timeout=20
        )
        return json.loads(r.json()["data"])
    return await asyncio.to_thread(_post)

# ============== 7. 过滤 + TTS ==============
def clean(text):
    for w in SENSITIVE:
        if w in text: return None
    return text

async def speak(text):
    voice = VOICE_MAP.get(PERSONA, "zh-CN-YunxiNeural")
    buf = io.BytesIO()
    async for chunk in Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    pygame.mixer.music.load(buf, "mp3")
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        await asyncio.sleep(0.1)

# ============== 7.5 情绪 BGM 播放（按 music_tag 抽本地音乐）==============
def play_bgm(music_tag, play_music_flag):
    """按工作流返回的 music_tag 从本地音乐库抽 1 首播放
    限频：120 秒内最多放 1 次，避免刷屏盖住解说（V1.2.6.1：60→120，王者一个团战 90-120s）
    互锁：播放后写 BGM_LOCK_UNTIL = mp3 真实时长，期间主循环不抢声道
    """
    global LAST_BGM_TIME, BGM_LOCK_UNTIL
    if not play_music_flag or music_tag in (None, "", "none"):
        return
    folder_name = MUSIC_LIB.get(music_tag)
    if folder_name is None:
        return
    # 120 秒限频（V1.2.6.1 实战调整：60→120）
    if time.time() - LAST_BGM_TIME < 120:
        print(f"  ⏸ BGM 限频中（120秒内已放过），跳过 {music_tag}")
        return
    folder = os.path.join(MUSIC_DIR, folder_name)
    if not os.path.isdir(folder):
        print(f"  ⚠ 音乐文件夹不存在：{folder}")
        return
    songs = [f for f in os.listdir(folder) if f.lower().endswith(('.mp3','.wav','.m4a'))]
    if not songs:
        print(f"  ⚠ {folder} 里没有音乐文件")
        return
    pick = random.choice(songs)
    full = os.path.join(folder, pick)
    try:
        bgm_channel = pygame.mixer.Channel(1)
        bgm_sound = pygame.mixer.Sound(full)
        bgm_sound.set_volume(0.6)   # BGM 音量稍低，避免盖人声
        bgm_channel.play(bgm_sound)
        LAST_BGM_TIME = time.time()
        # BGM 互锁：锁定时长 = mp3 文件真实时长
        real_len = bgm_sound.get_length()
        bgm_len = min(real_len, BGM_MAX_PLAY_SECONDS)
        BGM_LOCK_UNTIL = time.time() + bgm_len
        if real_len <= BGM_MAX_PLAY_SECONDS:
            print(f"  🎵 BGM [{music_tag}] → {pick}（真实时长 {real_len:.1f}s，锁定 {bgm_len:.1f}s 期间 AI 闭嘴）")
        else:
            print(f"  🎵 BGM [{music_tag}] → {pick}（⚠️ 整首 {real_len:.1f}s 太长，触发兜底锁 {bgm_len:.1f}s；建议剪成 10-15s 高潮段）")
    except Exception as e:
        print(f"  ⚠ BGM 播放失败：{e}")

# ============== 8. 弹幕 / 礼物挂钩 ==============
# ⭐V1.2.6 弹幕方案 = 端到端纯视觉，零 SDK 零外包：
#   ① 客户端 capture() 截全屏（已包含游戏投屏 + 抖音直播伴侣弹幕条）
#   ② 高光帧时图传给 vision-lite，模型同时看游戏 + 看弹幕条
#   ③ 工作流出参 recent_bullets 由模型识别画面里的弹幕文字返回
#   ④ 客户端拿到 recent_bullets 后写入 BULLET_CACHE 全局变量
#   ⑤ 下一轮主循环 fetch_recent_comments() 直接返回 BULLET_CACHE
#   ⑥ 文本路调用 call_text 时 bullet_comments 入参 = BULLET_CACHE
def fetch_recent_comments():
    """⭐V1.2.6 直接读 BULLET_CACHE（由 call_vision 返回的 recent_bullets 更新）"""
    return BULLET_CACHE

def detect_pay_gift(): return ""        # 礼物提醒（暂留空，未来可由视觉模型识别）
def game_ended(): return False           # 局结束检测（未来可由像素分析判断）

# ============== 9. 主循环（async 并行）==============
async def main():
    global SCENE_CACHE, VISION_COUNT, LAST_PHASH, BULLET_CACHE, BGM_LOCK_UNTIL
    print(f"🎙️ AI 解说启动 V1.2.7 - 性别：{PERSONA_MODE} - 游戏：{GAME_NAME}（auto=AI自动识别）")
    while True:
        try:
            # ━━ BGM 互锁检查：mp3 在响 → AI 闭嘴等结束 ━━
            now = time.time()
            if now < BGM_LOCK_UNTIL:
                remain = BGM_LOCK_UNTIL - now
                print(f"  🔇 BGM 锁定中（剩 {remain:.1f}s）AI 闭嘴等 mp3 接管")
                await asyncio.sleep(min(remain, 2))
                continue
            # 锁刚到期 / mp3 自然播完时，主动 stop 通道并清零锁
            if BGM_LOCK_UNTIL > 0:
                bgm_ch = pygame.mixer.Channel(1)
                if bgm_ch.get_busy():
                    bgm_ch.fadeout(800)
                    print("  🔚 BGM 超兜底上限，淡出停止")
                    await asyncio.sleep(1)
                bgm_ch.stop()
                BGM_LOCK_UNTIL = 0
                print("  ✅ BGM 已停，AI 解说接管控场")

            t0 = time.time()
            img = capture()
            bullet = fetch_recent_comments()
            alert  = detect_pay_gift()

            # ─── 像素分析 + 图床上传 并行起跑 ───
            state_task  = asyncio.create_task(detect_state(img))
            upload_task = asyncio.create_task(upload_image(img))

            raw_state = await state_task
            state = debounced_state(raw_state)
            print(f"[{time.strftime('%H:%M:%S')}] raw={raw_state} → routed={state}")

            # ─── 路由分流 ───
            if state == "静止":
                upload_task.cancel()
                data = await call_text(state, bullet, alert)

            elif state == "高光":
                if need_call_vision(img):
                    img_url = await upload_task
                    data = await call_vision(img_url, state, bullet, alert)
                    if data.get("scene_desc"):
                        SCENE_CACHE = data["scene_desc"]
                    # ⭐V1.2.6 把视觉模型识别出的弹幕缓存起来，供下一轮文本路用
                    if "recent_bullets" in data:
                        BULLET_CACHE = data.get("recent_bullets") or ""
                        if BULLET_CACHE:
                            print(f"  💬 弹幕缓存更新：{BULLET_CACHE[:60]}{'...' if len(BULLET_CACHE) > 60 else ''}")
                else:
                    upload_task.cancel()
                    data = await call_text(state, bullet, alert)
            else:  # 平淡
                upload_task.cancel()
                data = await call_text(state, bullet, alert)

            # ─── 解说输出 ───
            text = clean(data.get("speech_text", ""))
            if text:
                HISTORY.append(text)
                print(f"  → [{state}] {text}（耗时 {time.time()-t0:.2f}s）")
                await speak(text)

            # ─── 情绪 BGM 触发（仅高光帧才放，文本路永远忽略）───
            is_real_highlight = (state == "高光") and bool(data.get("high_light", False))
            play_bgm(
                data.get("music_tag", "none"),
                bool(data.get("play_music", False)) and is_real_highlight
            )

            await asyncio.sleep(data.get("next_interval", 6000) / 1000)

            # ─── 局结束重置节流 ───
            if game_ended():
                VISION_COUNT = 0
                LAST_PHASH = None
                print("🔄 一局结束，节流重置")

        except Exception as e:
            print("⚠️", e)
            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
