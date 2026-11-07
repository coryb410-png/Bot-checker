FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:20-alpine
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
# Logos, monkeys and other files in /public must ship with the standalone server
COPY --from=build /app/public ./public
EXPOSE 3000
# Next.js standalone binds to $HOSTNAME, which container hosts set to the pod's
# name. Force all interfaces so the host's proxy can reach it (fixes 502s).
ENV NODE_ENV=production
CMD ["sh", "-c", "HOSTNAME=0.0.0.0 exec node server.js"]
