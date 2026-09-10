data = open('isker.log', encoding='cp1251', errors='replace').readlines()
for line in data[-15:]:
    print(line.rstrip())