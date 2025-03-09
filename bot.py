import sys
sys.dont_write_bytecode = True

import asyncio
from aiogram import Bot
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import BOT_TOKEN, CHANNEL_ID
from playwright.async_api import async_playwright
import logging
import re
import time

# Полностью отключаем логирование
logging.basicConfig(
    level=logging.CRITICAL,
    handlers=[
        logging.NullHandler()
    ]
)

# Отключаем все логеры
for name in logging.root.manager.loggerDict:
    logging.getLogger(name).setLevel(logging.CRITICAL)
    logging.getLogger(name).propagate = False
    logging.getLogger(name).handlers = [logging.NullHandler()]

class FPIParser:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
        self.URL = "https://www.dextools.io/app/en/ton/pair-explorer/EQAyrrAjgSuyHrgGO1HimNbGV9tVLndZ3uocLaOyTw_FgegD"
        self.TOTAL_SUPPLY = 1_000_000_000  # 1 миллиард
        self.UPDATE_INTERVAL = 1  # Проверка каждые 10 секунд
        self.last_rate = None
        self.first_run = True
        self.last_check_time = 0
        
        # Настройки для индикаторов
        self.SHOW_PRICE_INDICATORS = True
        self.SHOW_MCAP_INDICATORS = True
        self.DECIMAL_PLACES = 5
        self.last_sent_rate = None  # Для отслеживания последнего отправленного значения
        
    async def get_fpi_data(self):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0 Safari/537.36'
                )
                page = await context.new_page()
                
                # Устанавливаем таймаут для ускорения
                await page.goto(self.URL, wait_until='domcontentloaded', timeout=15000)
                
                # Уменьшаем таймаут для ожидания селекторов
                price_selectors = [
                    'strong._ngcontent-ng-c1534065909',
                    'div.token-price',
                    'div.big-price',
                    'span[class*="ng-tns"][class*="ng-star-inserted"]',
                ]
                
                # Try to get the price
                rate = None
                for selector in price_selectors:
                    try:
                        await page.wait_for_selector(selector, timeout=3000)
                        elements = await page.query_selector_all(selector)
                        
                        for element in elements:
                            text = await element.inner_text()
                            if '$' in text or text.strip().startswith('0.'):
                                try:
                                    # Extract numbers from text
                                    price_text = re.search(r'(\d+\.\d+)', text.replace('$', '').replace(',', ''))
                                    if price_text:
                                        potential_rate = float(price_text.group(1))
                                        if 0.001 < potential_rate < 1:
                                            rate = potential_rate
                                            break
                                except ValueError:
                                    continue
                        
                        if rate:
                            break
                    except Exception:
                        continue
                
                await browser.close()
                
                if rate:
                    # Рассчитываем капитализацию напрямую из цены и общего предложения
                    market_cap = rate * self.TOTAL_SUPPLY
                    
                    # Форматируем капитализацию
                    if market_cap >= 1_000_000_000:  # Если больше 1 миллиарда
                        market_cap_formatted = f"${market_cap/1_000_000_000:.2f}B"
                    else:  # Иначе в миллионах
                        market_cap_formatted = f"${market_cap/1_000_000:.2f}M"
                    
                    return {
                        'rate': rate,
                        'market_cap': market_cap_formatted,
                        'mcap_value': market_cap
                    }
                else:
                    return None
                
        except Exception:
            return None

    def truncate_to_decimal_places(self, number, decimal_places):
        """Обрезает число до указанного количества десятичных знаков без округления"""
        str_value = str(number)
        if '.' in str_value:
            integer_part, decimal_part = str_value.split('.')
            truncated_decimal = decimal_part[:decimal_places]
            return float(f"{integer_part}.{truncated_decimal}")
        return number

    async def send_message(self, data):
        try:
            # Получаем обрезанное (не округленное) значение цены
            truncated_rate = self.truncate_to_decimal_places(data['rate'], self.DECIMAL_PLACES)
            
            # Определяем, изменилась ли цена с последнего отправленного сообщения
            price_changed = True  # По умолчанию - да (для первого запуска)
            
            if self.last_sent_rate is not None:
                # Проверяем, изменилась ли цена после обрезания до нужного количества знаков
                last_truncated = self.truncate_to_decimal_places(self.last_sent_rate, self.DECIMAL_PLACES)
                price_changed = last_truncated != truncated_rate
            
            # Проверяем, нужно ли отправлять сообщение (если цена изменилась или прошла минута)
            current_time = time.time()
            time_to_send = (current_time - self.last_check_time) >= 60  # минута в секундах
            
            if price_changed or time_to_send or self.first_run:
                # Определяем индикатор для цены
                price_indicator = ""
                if self.SHOW_PRICE_INDICATORS:
                    price_indicator = "🟩"  # По умолчанию зеленый квадрат для первого запуска
                    if not self.first_run and self.last_rate is not None:
                        if data['rate'] > self.last_rate:
                            price_indicator = "🟩"  # Зеленый квадрат при повышении цены
                        elif data['rate'] < self.last_rate:
                            price_indicator = "🟥"  # Красный квадрат при понижении цены
                
                # Определяем индикатор для капитализации
                mcap_indicator = ""
                if self.SHOW_MCAP_INDICATORS:
                    if self.first_run:
                        mcap_indicator = "🟩"  # Зеленый квадрат при первом запуске
                    elif self.last_rate is not None:
                        if data['rate'] > self.last_rate:
                            mcap_indicator = "🟩"  # Капитализация растет вместе с ценой
                        elif data['rate'] < self.last_rate:
                            mcap_indicator = "🟥"  # Капитализация падает вместе с ценой
                
                # Форматируем цену без округления, а с обрезанием до 4 знаков
                rate_str = str(truncated_rate)
                if '.' in rate_str:
                    integer_part, decimal_part = rate_str.split('.')
                    # Обеспечиваем ровно 4 знака после запятой
                    if len(decimal_part) < self.DECIMAL_PLACES:
                        decimal_part = decimal_part.ljust(self.DECIMAL_PLACES, '0')
                    else:
                        decimal_part = decimal_part[:self.DECIMAL_PLACES]
                    formatted_rate = f"{integer_part}.{decimal_part}"
                else:
                    formatted_rate = f"{rate_str}.{'0' * self.DECIMAL_PLACES}"
                
                # Формируем сообщение с добавлением индикаторов
                message = (
                    f"{price_indicator}FPIBANK = {formatted_rate}$\n"
                    f"{mcap_indicator}MCap = {data['market_cap']}\n"
                    f"@price_FPIBANK"
                )
                
                # Отправляем сообщение
                await self.bot.send_message(CHANNEL_ID, message)
                
                # Обновляем время последней проверки
                self.last_check_time = current_time
                
                # Сохраняем текущее значение для следующего сравнения
                self.last_rate = data['rate']
                self.last_sent_rate = data['rate']  # Запоминаем последнее отправленное значение
                self.first_run = False
        except Exception:
            pass

    async def check_and_send_rate(self):
        current_data = await self.get_fpi_data()
        if current_data:
            await self.send_message(current_data)

    def start(self):
        try:
            # Установка времени первого запуска
            self.last_check_time = time.time()
            
            # Начинаем мониторинг сразу
            asyncio.get_event_loop().create_task(self.check_and_send_rate())
            
            # Запускаем мониторинг каждые 10 секунд для более частых проверок
            self.scheduler.add_job(self.check_and_send_rate, 'interval', seconds=self.UPDATE_INTERVAL)
            self.scheduler.start()
            asyncio.get_event_loop().run_forever()
        except Exception:
            pass

if __name__ == "__main__":
    parser = FPIParser()
    try:
        parser.start()
    except KeyboardInterrupt:
        pass
    except Exception:
        pass