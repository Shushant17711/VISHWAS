from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

opts = webdriver.ChromeOptions()
opts.add_argument("--headless=new")
opts.add_argument("--disable-gpu")
opts.add_argument("--no-sandbox")
opts.add_argument("--window-size=1600,3200")
driver = webdriver.Chrome(options=opts)
try:
    driver.get("http://127.0.0.1:8000/")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".views__tab")))
    tabs = driver.find_elements(By.CSS_SELECTOR, ".views__tab")
    for t in tabs:
        if t.text.strip().lower() == "evidence":
            t.click()
            break
    time.sleep(2.5)  # let loadResults() fetch + render
    body = driver.find_element(By.ID, "results-body")
    print("results-body text preview:", body.text[:300])
    # full page screenshot
    total_height = driver.execute_script("return document.body.scrollHeight")
    driver.set_window_size(1600, max(total_height + 100, 1200))
    time.sleep(0.3)
    driver.save_screenshot(r"C:\Users\shush\OneDrive\Documents\VISHWAS\scratch\shots\evidence_full.png")
    print("saved, page height:", total_height)
finally:
    driver.quit()
