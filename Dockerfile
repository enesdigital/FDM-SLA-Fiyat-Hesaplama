FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ENV VITE_BASE=/
RUN npm run build
FROM nginx:stable-alpine
COPY --chmod=644 deploy/nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
ENTRYPOINT []
CMD ["nginx", "-g", "daemon off;"]
