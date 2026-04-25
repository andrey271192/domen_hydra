# 🌐 HydraRoute Manager

Веб-интерфейс для централизованного управления конфигурацией **HydraRoute Neo** на роутерах Keenetic. Без мониторинга, без Telegram — только домены и IP.

![Превью веб-интерфейса](docs/preview.png)

---

## Интерфейс

### Вкладка «Конфигурация» — группы доменов и IP

Карточки с переключателями вкл/выкл, политикой маршрутизации (HydraRoute, RU и т.д.), быстрым редактированием и кнопкой **«Обновить все роутеры»**.

### Вкладка «Импорт файлов»

Два текстовых поля для вставки содержимого `domain.conf` и `ip.list` с роутера, кнопки **«Сохранить на сервер»** и **«Сохранить + обновить все роутеры»**, ниже — экспорт текущей конфигурации для копирования.

### Вкладка «Роутеры»

Список роутеров с IP и SSH-паролем для массовой отправки конфига через `sshpass` + `curl` на каждый роутер.

### Вкладка «Настройки»

Смена пароля администратора веб-интерфейса (запись в `server/.env`).

### Экран входа

При открытии сайта запрашивается пароль из `ADMIN_PASSWORD` в `.env`.

---

## Возможности

- Управление группами доменов и IP через браузер
- Включение / отключение групп одним переключателем
- Импорт `domain.conf` и `ip.list` с роутера
- Отправка конфига на все роутеры одной кнопкой (SSH + `curl` с сервера)
- Авторизация по паролю
- Прямые ссылки для роутера: `/hydra/domain.conf`, `/hydra/ip.list`, `/hydra/version`

---

## Установка сервера (Ubuntu 22/24)

Сначала перейди в **существующий** каталог (если ты был в удалённой папке вроде `/opt/keenetic-unified`, `git` выдаст `Unable to read current working directory` — тогда выполни `cd /opt` или `cd /`).

```bash
cd /opt
git clone https://github.com/andrey271192/domen_hydra.git /opt/domen-hydra
cd /opt/domen-hydra
bash server/install.sh
nano server/.env   # при необходимости поправь плейсхолдеры (см. ниже)
systemctl restart hydra-manager
```

Сервис `hydra-manager` появляется **только после** успешного `bash server/install.sh`; сообщение `Unit hydra-manager.service not found` значит, что установка не дошла до конца (часто из‑за ошибки `git clone` выше).

Скрипт `server/install.sh` при первом запуске **сам создаёт** `server/.env` из `server/.env.example` с уже заполненными полями (вместо паролей — текст-напоминание, его нужно заменить на свои секреты).

### Файл `server/.env`

| Переменная | Назначение |
|------------|------------|
| `HOST` | Адрес привязки HTTP (обычно `0.0.0.0`). |
| `PORT` | Порт веб-интерфейса (по умолчанию `8000`). |
| `ADMIN_PASSWORD` | Пароль входа в веб-интерфейс. |
| `SSH_USER` | Пользователь SSH для «Обновить все роутеры». |
| `SSH_PASS` | Пароль SSH по умолчанию (на карточке роутера можно задать свой). |

Шаблон по умолчанию (после установки открой `nano server/.env` и подставь реальные пароли):

```env
HOST=0.0.0.0
PORT=8000
ADMIN_PASSWORD=ВАШ_НАДЁЖНЫЙ_ПАРОЛЬ_ВЕБ

SSH_USER=root
SSH_PASS=ПАРОЛЬ_SSH_РОУТЕРА
```

Интерфейс: `http://ВАШ_IP:PORT` — значения `HOST` и `PORT` из `server/.env` подставляются в `hydra-manager.service` при запуске `server/install.sh`. Если поменяешь порт в `.env` уже после установки, снова выполни `bash server/install.sh` (или вручную обнови `ExecStart` в unit и `systemctl daemon-reload`).

---

## Установка на роутер (Keenetic + Entware)

Подставь URL своего сервера:

```bash
export SERVER_URL="http://ВАШ_IP:8000" \
  && curl -fsSL https://raw.githubusercontent.com/andrey271192/domen_hydra/main/install_router.sh | sh
```

Роутер будет каждый день в **02:00** скачивать актуальный конфиг с сервера.

Ручное обновление:

```bash
sh /opt/bin/hydra_update.sh
```

---

## Как пользоваться

1. Открой веб-интерфейс → введи пароль из `.env`
2. **Импорт**: вставь `domain.conf` и `ip.list` с роутера → «Сохранить на сервер»
3. Редактируй группы на вкладке «Конфигурация»
4. Нажми **«Обновить все роутеры»** — файлы запишутся в `/opt/etc/HydraRoute/` и выполнится `neo restart`

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

docs/
  preview.png      — превью интерфейса

install_router.sh  — установка на роутер
hydra_update.sh    — скачивание конфига с сервера
```

---

## Обновление

```bash
cd /opt/domen-hydra && git pull && systemctl restart hydra-manager
```

---

## Поддержка проекта

[Boosty — донат](https://boosty.to/andrey27/donate). На GitHub доступна кнопка **Sponsor** (`.github/FUNDING.yml`).

**Связь:** [Telegram @Iot_andrey](https://t.me/Iot_andrey) — вопросы и обратная связь.

---

## Связь с Keenetic Unified

Логика парсинга `domain.conf` / `ip.list` и веб-интерфейс доменов взяты из проекта [keenetic-unified](https://github.com/andrey271192/keenetic-unified) в урезанном виде: только управление HydraRoute, без дашборда, watchdog и Telegram.
