# 🌐 HydraRoute Manager

Веб-интерфейс для централизованного управления конфигурацией **HydraRoute Neo** на роутерах Keenetic. Без мониторинга, без Telegram — только домены и IP.

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

```bash
git clone https://github.com/andrey271192/domen_hydra.git /opt/domen-hydra
cd /opt/domen-hydra
bash server/install.sh
nano server/.env   # задать ADMIN_PASSWORD
systemctl restart hydra-manager
```

Интерфейс: `http://IP:8000`

---

## Установка на роутер (Keenetic + Entware)

```bash
export SERVER_URL="http://IP_СЕРВЕРА:8000" \
  && curl -fsSL https://raw.githubusercontent.com/andrey271192/domen_hydra/main/install_router.sh | sh
```

Роутер будет каждый день в **02:00** скачивать актуальный конфиг с сервера.

Ручное обновление:

```bash
sh /opt/bin/hydra_update.sh
```

---

## Как пользоваться

1. Открой `http://IP:8000` → введи пароль из `.env`
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

Развитие можно поддержать на [Boosty — донат](https://boosty.to/andrey27/donate). На GitHub у репозитория отображается кнопка **Sponsor** (файл `.github/FUNDING.yml`).

---

## Связь с Keenetic Unified

Логика парсинга `domain.conf` / `ip.list` и веб-интерфейс доменов взяты из проекта [keenetic-unified](https://github.com/andrey271192/keenetic-unified) в урезанном виде: только управление HydraRoute, без дашборда, watchdog и Telegram.

поддержать автора 
https://boosty.to/andrey27/donate
