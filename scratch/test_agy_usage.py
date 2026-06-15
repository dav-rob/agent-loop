import subprocess
import sys

def test_agy_usage():
    try:
        res = subprocess.run(
            ["antigravity-usage", "--version"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL
        )
        print("Version output:", res.stdout, res.stderr)
        
        res_quota = subprocess.run(
            ["antigravity-usage", "quota", "--json", "--method", "google"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL
        )
        print("Quota stdout:", res_quota.stdout)
        print("Quota stderr:", res_quota.stderr)
        print("Quota returncode:", res_quota.returncode)
    except Exception as e:
        print("Exception:", e)

if __name__ == "__main__":
    test_agy_usage()
