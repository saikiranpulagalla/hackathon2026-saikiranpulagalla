import asyncio, time
t0 = time.time()
async def worker(id):
    print(f"Start {id} at {time.time()-t0:.1f}")
    await asyncio.sleep(1)

async def main():
    tasks = []
    for i in range(3):
        tasks.append(worker(i))
        await asyncio.sleep(1)
    await asyncio.gather(*tasks)

asyncio.run(main())
