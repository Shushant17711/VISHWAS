from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

opts = webdriver.ChromeOptions()
opts.add_argument("--headless=new")
opts.add_argument("--disable-gpu")
opts.add_argument("--no-sandbox")
opts.add_argument("--window-size=1600,1000")
driver = webdriver.Chrome(options=opts)
try:
    driver.get("http://127.0.0.1:8000/")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "f-launch")))
    driver.find_element(By.ID, "f-ticks").clear()
    driver.find_element(By.ID, "f-ticks").send_keys("300")
    driver.find_element(By.ID, "f-launch").click()
    WebDriverWait(driver, 15).until(EC.visibility_of_element_located((By.ID, "transport")))
    time.sleep(3)
    logs = [l for l in driver.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]]
    print("console errors:", logs)
    driver.save_screenshot(r"C:\Users\shush\OneDrive\Documents\VISHWAS\scratch\shots\running.png")
    print("saved")
finally:
    driver.quit()
