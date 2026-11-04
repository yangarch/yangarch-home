"""First-party logging and one narrowly scoped public collection endpoint."""
HTTP_CONFIG='''# Only this apex site uses these variables and this log format.
map "$request_method:$uri" $home_method_allowed {
    default 0;
    ~^(GET|HEAD): 1;
    "POST:/__analytics" 1;
}
map $remote_addr $home_loggable { default 1; 127.0.0.1 0; ::1 0; }
map $cookie_home_analytics_optout $home_excluded { default 0; 1 1; }
map $http_user_agent $home_client {
    default visitor;
    ~*(bot|crawler|spider|curl|wget|python|headless|uptime|monitor) automated;
}
map $http_referer $home_referrer {
    default "";
    "~^(?<home_ref_origin>https?://[A-Za-z0-9.-]+(?::[0-9]+)?)(?:/|$)" $home_ref_origin;
}
log_format home_events escape=json '{"v":1,"host":"yangarch.net","time":"$time_iso8601","ip":"$remote_addr","method":"$request_method","path":"$uri","status":$status,"bytes":$body_bytes_sent,"seconds":$request_time,"referrer":"$home_referrer","ua":"$http_user_agent","client":"$home_client","excluded":"$home_excluded"}';
limit_req_zone $binary_remote_addr zone=yangarch_events:1m rate=2r/s;
'''
COLLECTOR='''    location = /__analytics {
        if ($request_method != POST) { return 405; }
        client_max_body_size 2k;
        limit_req zone=yangarch_events burst=15 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:4318/collect;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host 127.0.0.1;
        proxy_set_header Origin $http_origin;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For "";
        proxy_set_header Cookie "";
        proxy_set_header Authorization "";
        proxy_set_header DNT $http_dnt;
        proxy_set_header Sec-GPC $http_sec_gpc;
        proxy_connect_timeout 2s;
        proxy_read_timeout 10s;
        proxy_send_timeout 10s;
    }
'''
LOG_LINE='    access_log /var/log/yangarch-home/access.log home_events if=$home_loggable;\n'
