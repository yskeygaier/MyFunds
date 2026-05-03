import akshare as ak
fund_funcs = [x for x in dir(ak) if 'fund' in x.lower()]
for f in fund_funcs:
    if any(keyword in f.lower() for keyword in ['holder', 'manager', 'portfolio', 'position', 'stock', 'industry', 'scale', 'rating', 'risk', 'performance']):
        print(f)
