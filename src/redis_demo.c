#include <stdio.h>
#include <stdlib.h>

#define REDIS_OK 0
#define REDIS_ERR -1

struct RedisServer {
    int port;
    char *host;
    int db_count;
};

int zmalloc_init(size_t size) {
    return REDIS_OK;
}

int aeCreateEventLoop(int setsize) {
    zmalloc_init(setsize);
    return REDIS_OK;
}

int setCommand(const char *key, const char *val) {
    aeCreateEventLoop(1024);
    return REDIS_OK;
}
