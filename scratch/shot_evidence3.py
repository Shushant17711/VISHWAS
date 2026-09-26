import base64
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

opts = webdriver.ChromeOptions()
opts.add_argument("--headless=new")
opts.add_argument("--disable-gpu")
opts.add_argument("--no-sandbox")
opts.add_argument("--window-size=1600,1200")
driver = webdriver.Chrome(options=opts)
try:
    driver.get("http://127.0.0.1:8000/")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".views__tab")))
    for t in driver.find_elements(By.CSS_SELECTOR, ".views__tab"):
        if t.text.strip().lower() == "evidence":
            t.click()
            break
    WebDriverWait(driver, 10).until(
        lambda d: "CUSUM" in d.find_element(By.ID, "results-body").text.upper()
    )
    time.sleep(0.5)

    # .sheet scrolls internally (fixed-height app shell) - force it to flow
    # naturally so scrollHeight/screenshot capture everything, not just one
    # viewport's worth.
    driver.execute_script("""
        document.documentElement.style.height = 'auto';
        document.body.style.height = 'auto';
        document.querySelector('.sheet').style.overflow = 'visible';
        document.querySelector('.sheet').style.height = 'auto';
    """)
    time.sleep(0.3)

    metrics = driver.execute_cdp_cmd("Page.getLayoutMetrics", {})
    height = int(metrics["cssContentSize"]["height"])
    width = int(metrics["cssContentSize"]["width"])
    print("content size:", width, height)

    driver.execute_cdp_cmd(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1600, "height": height, "deviceScaleFactor": 1, "mobile": False},
    )
    result = driver.execute_cdp_cmd(
        "Page.captureScreenshot",
        {"format": "png", "captureBeyondViewport": True,
         "clip": {"x": 0, "y": 0, "width": 1600, "height": height, "scale": 1}},
    )
    with open(r"C:\Users\shush\OneDrive\Documents\VISHWAS\scratch\shots\evidence3_full.png", "wb") as f:
        f.write(base64.b64decode(result["data"]))
    print("saved full page")
finally:
    driver.quit()
