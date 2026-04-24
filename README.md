# 🌐 HydraRoute Manager

Веб-интерфейс для централизованного управления конфигурацией **HydraRoute Neo** на роутерах Keenetic.

- Управление группами доменов и IP через браузер
- Включение / отключение групп одним переключателем
- Импорт `domain.conf` и `ip.list` с роутера
- Отправка конфига на все роутеры одной кнопкой
- Авторизация по паролю
- Скачивание файлов для роутера по HTTP

---

## Установка сервера (Ubuntu 22/24)

```bash
git clone https://github.com/andrey271192/domen_hydra.git /opt/domen-hydra
cd /opt/domen-hydra
bash server/install.sh
nano server/.env   # задать ADMIN_PASSWORD
systemctl restart hydra-manager
```

Интерфейс откроется на `http://IP:8000`

---

## Установка на роутер (Keenetic + Entware)

```bash
export SERVER_URL="http://IP_СЕРВЕРА:8000" \
  && curl -fsSL https://raw.githubusercontent.com/andrey271192/domen_hydra/main/install_router.sh | sh
```

Роутер будет каждый день в 02:00 скачивать актуальный конфиг с сервера.

Ручное обновление:
```bash
sh /opt/bin/hydra_update.sh
```

---

## Как использовать

1. Открой `http://IP:8000` → введи пароль
2. **Импорт**: вставь `domain.conf` и `ip.list` с роутера → "Сохранить на сервер"
3. Редактируй группы доменов и IP прямо в браузере
4. Нажми **"📡 Обновить все роутеры"** — конфиг применится через SSH

---

## Формат файлов

**domain.conf:**
```
##youtube
youtube.com,youtu.be,googlevideo.com/HydraRoute

##avito
avito.ru,ozon.ru/RU
```

**ip.list:**
```
##geoip:ru
/RU
geoip:ru
```

---

## Структура проекта

```
server/
  main.py          — FastAPI приложение
  config.py        — настройки из .env
  hydra_manager.py — парсинг/генерация domain.conf + ip.list
  models.py        — модели данных
  database.py      — работа с JSON
  install.sh       — установка на Ubuntu
  .env.example     — шаблон конфигурации
  templates/
    index.html     — веб-интерфейс

install_router.sh  — установка на роутер
```

---

## Обновление

```bash
cd /opt/domen-hydra && git pull && systemctl restart hydra-manager
```
