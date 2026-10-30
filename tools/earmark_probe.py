"""Oct 29 (read-only): what GAO's Community Project Funding / Congressionally Directed Spending file holds
(FY2022-2024): files, columns, a few rows, and how requesting members are written. Nothing is saved."""
import io, zipfile, requests, pandas as pd
URL = "https://www.gao.gov/assets/2026-07/GAO_Tracking_the_Funds_data_2022-2024_GAO-26-107944.zip"
r = requests.get(URL, timeout=120, headers={"User-Agent": "Mozilla/5.0 (research; congress-signals)"})
print("HTTP", r.status_code, len(r.content), r.headers.get("content-type"))
z = zipfile.ZipFile(io.BytesIO(r.content))
for n in z.namelist():
    print("FILE", n, z.getinfo(n).file_size)
for n in z.namelist():
    if n.lower().endswith((".xlsx", ".xls", ".csv")):
        if n.lower().endswith(".csv"):
            sheets = {"csv": pd.read_csv(z.open(n), nrows=2000, low_memory=False)}
        else:
            sheets = pd.read_excel(z.open(n), sheet_name=None, nrows=2000)
        for sh, df in sheets.items():
            print(f"\n== {n} / {sh}: {df.shape}")
            print(list(df.columns))
            print(df.head(4).to_string()[:2500])
