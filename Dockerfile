FROM python:3.13.7-alpine
COPY . .
RUN pip install -r requirements.txt
WORKDIR /app
RUN apk add --no-cache bash
RUN pip install -r requirements.txt
EXPOSE 5000
ENTRYPOINT [ "python" ]
CMD [ "portable.py" ]
